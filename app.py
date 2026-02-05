from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import psutil
import os
import datetime
import random
import string
import socket
import subprocess
import requests
import json
import uuid
import base64
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config['SECRET_KEY'] = 'bansos-zivpn-secret-key-change-me'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

@app.template_filter('from_json')
def from_json(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except:
        return None

# --- Models ---
class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    paymenku_api_key = db.Column(db.String(255), nullable=True)
    price_per_month = db.Column(db.Integer, default=10000)

class Server(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    ip_address = db.Column(db.String(50), nullable=False)
    domain = db.Column(db.String(100), nullable=True) # Node domain
    country = db.Column(db.String(50), default='Unknown')
    isp = db.Column(db.String(100), default='Unknown')
    token = db.Column(db.String(100), unique=True, nullable=False)
    last_heartbeat = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='offline') # online, offline
    stats = db.Column(db.Text, nullable=True) # JSON string: {cpu: 10, ram: 20}

class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(80), nullable=False) # Storing plain for user display

    # New Fields for Multi-Server & Protocols
    protocol = db.Column(db.String(20), default='udp') # udp, vmess, vless, trojan
    uuid = db.Column(db.String(36), nullable=True) # For Xray
    server_id = db.Column(db.Integer, db.ForeignKey('server.id'), nullable=True)
    server = db.relationship('Server', backref=db.backref('accounts', lazy=True))

    ip_address = db.Column(db.String(50), nullable=True) # Valid for UDP, or redundant if using Server relation
    pin = db.Column(db.String(10), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    expiry_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='pending')

    # Paymenku specific
    reference_id = db.Column(db.String(100), unique=True, nullable=True)
    trx_id = db.Column(db.String(100), nullable=True)

# --- Helper Functions ---
def get_system_stats():
    # Stats for the MASTER server (Panel)
    cpu_percent = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    ram_percent = ram.percent

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except:
        local_ip = "127.0.0.1"

    return {
        'cpu': cpu_percent,
        'ram': ram_percent,
        'ip': local_ip,
        'domain': request.host if request else 'localhost'
    }

def init_db():
    with app.app_context():
        # Check if we need to migrate (simple hack: check if Server table exists)
        try:
            Server.query.first()
        except:
            # If error, tables might be missing or old schema.
            # In dev, we can try to create.
            # If it fails due to mismatch, we might need to recreate DB file manually or catch operational error.
            pass

        db.create_all()

        # Create Default Localhost Server if none exists
        if not Server.query.first():
            local_server = Server(name="Localhost", ip_address="127.0.0.1", token=str(uuid.uuid4()), status="online")
            # Fake stats for localhost
            local_server.stats = json.dumps({'cpu': 0, 'ram': 0})
            db.session.add(local_server)

        # Create Localhost Server (The Panel itself acts as a server too if needed, or just default)
        # But per plan, we treat this as Master. Users might want to install VPN on Master too.
        # Let's check if Admin exists
        if not Admin.query.filter_by(username='admin').first():
            admin = Admin(username='admin')
            admin.set_password('admin123')
            db.session.add(admin)

        if not Settings.query.first():
            settings = Settings(paymenku_api_key='')
            db.session.add(settings)

        db.session.commit()

def get_paymenku_channels(api_key):
    if not api_key:
        return []
    try:
        headers = {'Authorization': f'Bearer {api_key}'}
        response = requests.get('https://paymenku.com/api/v1/payment-channels', headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data if isinstance(data, list) else data.get('data', [])
    except:
        pass
    return []

def generate_config_uri(account):
    host = account.server.domain or account.server.ip_address
    name = f"{account.username}-{account.server.name}"

    # Defaults to port 443 (TLS) or 80 (Non-TLS).
    # For generated links, we prefer TLS (443).
    # If the user wants non-TLS, they can usually edit the config or we could provide a switch.
    # Given the request "pastikan port akun suport tls non tls", Nginx handles both.
    # We will generate the TLS config as the default "Premium" link.
    port = 443
    tls_settings = "tls"

    if account.protocol == 'vmess':
        # Vmess JSON format
        conf = {
            "v": "2",
            "ps": name,
            "add": host,
            "port": port,
            "id": account.uuid,
            "aid": "0",
            "net": "ws",
            "type": "none",
            "host": host,
            "path": "/vmess",
            "tls": tls_settings
        }
        return "vmess://" + base64.b64encode(json.dumps(conf).encode('utf-8')).decode('utf-8')

    elif account.protocol == 'vless':
        # vless://uuid@host:port?security=tls&encryption=none&type=ws&host=host&path=/vless#name
        return f"vless://{account.uuid}@{host}:{port}?security={tls_settings}&encryption=none&type=ws&host={host}&path=/vless#{name}"

    elif account.protocol == 'trojan':
        # trojan://password@host:port?security=tls&type=ws&host=host&path=/trojan#name
        return f"trojan://{account.uuid}@{host}:{port}?security={tls_settings}&type=ws&host={host}&path=/trojan#{name}"

    return None

# --- User Routes ---
@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    stats = get_system_stats()
    settings = Settings.query.first()

    # Get active servers
    servers = Server.query.filter_by(status='online').all()
    # Or just all servers for now so user can see them even if offline (maybe disabled)
    all_servers = Server.query.all()

    channels = []
    if settings and settings.paymenku_api_key:
        channels = get_paymenku_channels(settings.paymenku_api_key)

    if not channels:
        channels = [
            {'code': 'qris', 'name': 'QRIS', 'type': 'qris'},
            {'code': 'bca_va', 'name': 'BCA Virtual Account', 'type': 'va'},
            {'code': 'dana', 'name': 'DANA', 'type': 'ewallet'}
        ]

    return render_template('dashboard.html', stats=stats, settings=settings, channels=channels, servers=all_servers)

@app.route('/buy', methods=['POST'])
def buy():
    username = request.form.get('username')
    password = request.form.get('password')
    channel_code = request.form.get('channel_code')
    protocol = request.form.get('protocol', 'udp')
    server_id = request.form.get('server_id')
    duration = int(request.form.get('duration', 30))

    if not username: # Password might be auto-generated for uuid protocols, but let's stick to form
        flash('Username wajib diisi!', 'danger')
        return redirect(url_for('dashboard'))

    if Account.query.filter_by(username=username).first():
        flash('Username sudah digunakan. Pilih yang lain.', 'warning')
        return redirect(url_for('dashboard'))

    settings = Settings.query.first()
    if not settings or not settings.paymenku_api_key:
        flash('Sistem pembayaran belum dikonfigurasi admin.', 'danger')
        return redirect(url_for('dashboard'))

    if not server_id:
        flash('Silakan pilih server.', 'warning')
        return redirect(url_for('dashboard'))

    server = Server.query.get(server_id)
    if not server:
        flash('Server tidak ditemukan.', 'danger')
        return redirect(url_for('dashboard'))

    # Prepare Account Data
    pin = str(random.randint(100000, 999999))
    while Account.query.filter_by(pin=pin).first():
        pin = str(random.randint(100000, 999999))

    reference_id = f"INV-{int(datetime.datetime.utcnow().timestamp())}-{random.randint(100,999)}"
    expiry = datetime.datetime.utcnow() + datetime.timedelta(days=duration)

    new_uuid = None
    if protocol in ['vmess', 'vless', 'trojan']:
        new_uuid = str(uuid.uuid4())
        # For these protocols, password might be less relevant for auth but used for user access
        if not password:
            password = "generated-uuid"
    else:
        # UDP requires password
        if not password:
             flash('Password wajib diisi untuk UDP.', 'danger')
             return redirect(url_for('dashboard'))

    new_account = Account(
        username=username,
        password=password,
        pin=pin,
        expiry_date=expiry,
        status='pending',
        reference_id=reference_id,
        protocol=protocol,
        server_id=server.id,
        uuid=new_uuid
    )

    db.session.add(new_account)
    db.session.commit()

    # Calculate total price based on duration
    # Assuming price_per_month is for 30 days
    total_amount = int((settings.price_per_month / 30) * duration)

    # Call Paymenku API (Same as before)
    payload = {
        "reference_id": reference_id,
        "amount": total_amount,
        "customer_name": username,
        "customer_email": "user@zivpn.local",
        "customer_phone": "08123456789",
        "channel_code": channel_code if channel_code else "qris",
        "return_url": url_for('success', account_id=new_account.id, _external=True)
    }

    headers = {
        "Authorization": f"Bearer {settings.paymenku_api_key}",
        "Content-Type": "application/json"
    }

    try:
        r = requests.post("https://paymenku.com/api/v1/transaction/create", json=payload, headers=headers, timeout=10)
        resp_data = r.json()

        is_success = (r.status_code == 200) and (
            resp_data.get('success') is True or
            resp_data.get('status') == 'success'
        )

        if is_success:
            data = resp_data.get('data', resp_data)
            payment_url = (
                data.get('pay_url') or
                data.get('payment_url') or
                data.get('redirect_url')
            )

            if not payment_url and 'payment_info' in data:
                info = data['payment_info']
                payment_url = info.get('payment_page') or info.get('qr_url')

            if payment_url:
                return redirect(payment_url)
            else:
                flash(f"Error getting payment URL: {resp_data}", 'danger')
        else:
            flash(f"Payment Gateway Error: {resp_data.get('message', r.text)}", 'danger')

    except Exception as e:
        flash(f"Connection Error: {str(e)}", 'danger')

    db.session.delete(new_account)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/callback', methods=['POST'])
def payment_callback():
    data = request.json
    if not data:
        return jsonify({'status': 'no data'}), 400

    reference_id = data.get('reference_id')
    status = data.get('status')
    trx_id = data.get('trx_id')

    if reference_id and status == 'paid':
        account = Account.query.filter_by(reference_id=reference_id).first()
        if account:
            account.status = 'active'
            account.trx_id = trx_id
            db.session.commit()
            return jsonify({'success': True})

    return jsonify({'success': False}), 200

@app.route('/success/<int:account_id>')
def success(account_id):
    account = Account.query.get_or_404(account_id)
    config_uri = generate_config_uri(account)
    if account.status != 'active':
        flash('Pembayaran sedang diproses. Tunggu sebentar atau refresh.', 'info')
    return render_template('success.html', pin=account.pin, account=account, config_uri=config_uri)

@app.route('/list', methods=['GET', 'POST'])
def list_accounts():
    account = None
    config_uri = None
    if request.method == 'POST':
        pin = request.form.get('pin')
        if pin:
            account = Account.query.filter_by(pin=pin).first()
            if not account:
                flash('PIN tidak ditemukan.', 'danger')
            elif account.status != 'active':
                flash('Akun belum aktif atau sudah kadaluarsa.', 'warning')
                account = None
            else:
                config_uri = generate_config_uri(account)

    return render_template('list.html', account=account, config_uri=config_uri)

# --- Admin Routes ---
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        admin = Admin.query.filter_by(username=username).first()
        if admin and admin.check_password(password):
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Login gagal. Cek username dan password.', 'danger')

    return render_template('admin_login.html')

@app.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    accounts = Account.query.order_by(Account.created_at.desc()).all()
    settings = Settings.query.first()
    servers = Server.query.all()
    return render_template('admin_dashboard.html', accounts=accounts, settings=settings, servers=servers)

@app.route('/admin/create_account', methods=['POST'])
def admin_create_account():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    username = request.form.get('username')
    password = request.form.get('password')
    protocol = request.form.get('protocol')
    server_id = request.form.get('server_id')
    duration = int(request.form.get('duration', 30))

    if Account.query.filter_by(username=username).first():
        flash('Username sudah digunakan.', 'danger')
        return redirect(url_for('admin_dashboard'))

    server = Server.query.get(server_id)
    if not server:
        flash('Server tidak valid.', 'danger')
        return redirect(url_for('admin_dashboard'))

    # Logic similar to buy() but no payment
    pin = str(random.randint(100000, 999999))
    while Account.query.filter_by(pin=pin).first():
        pin = str(random.randint(100000, 999999))

    reference_id = f"ADM-{int(datetime.datetime.utcnow().timestamp())}-{random.randint(100,999)}"
    expiry = datetime.datetime.utcnow() + datetime.timedelta(days=duration)

    new_uuid = None
    if protocol in ['vmess', 'vless', 'trojan']:
        new_uuid = str(uuid.uuid4())
        if not password:
            password = "generated-uuid"
    else:
        # UDP
        if not password:
             flash('Password wajib diisi untuk UDP.', 'danger')
             return redirect(url_for('admin_dashboard'))

    new_account = Account(
        username=username,
        password=password,
        pin=pin,
        expiry_date=expiry,
        status='active', # Directly active
        reference_id=reference_id,
        protocol=protocol,
        server_id=server.id,
        uuid=new_uuid
    )

    db.session.add(new_account)
    db.session.commit()
    flash(f'Akun {username} berhasil dibuat (Status: Active).', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/action/<action>/<int:id>', methods=['POST'])
def admin_action(action, id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    account = Account.query.get_or_404(id)

    if action == 'delete':
        db.session.delete(account)
        flash('Akun berhasil dihapus.', 'success')
    elif action == 'extend':
        account.expiry_date += datetime.timedelta(days=30)
        flash('Masa aktif akun diperpanjang 30 hari.', 'success')

    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/edit_account/<int:id>', methods=['GET', 'POST'])
def edit_account(id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    account = Account.query.get_or_404(id)

    if request.method == 'POST':
        account.password = request.form.get('password')
        account.status = request.form.get('status')
        db.session.commit()
        flash('Akun berhasil diperbarui.', 'success')
        return redirect(url_for('admin_dashboard'))

    return render_template('edit_account.html', account=account)

@app.route('/admin/update_settings', methods=['POST'])
def update_settings():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    settings = Settings.query.first()
    settings.paymenku_api_key = request.form.get('paymenku_api_key')

    try:
        settings.price_per_month = int(request.form.get('price'))
    except:
        pass

    db.session.commit()
    flash('Pengaturan berhasil disimpan.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/change_password', methods=['POST'])
def change_password():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    new_password = request.form.get('new_password')
    if new_password:
        admin = Admin.query.filter_by(username='admin').first()
        if admin:
            admin.set_password(new_password)
            db.session.commit()
            flash('Password admin berhasil diubah.', 'success')

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/system_update', methods=['POST'])
def system_update():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    repo_url = "https://github.com/2026musik-code/bansoudp.git"
    branch = "bansos-zivpn-web-14439126923524383263"

    # Locate git binary
    git_cmd = 'git'
    if os.path.exists('/usr/bin/git'):
        git_cmd = '/usr/bin/git'
    elif os.path.exists('/usr/local/bin/git'):
        git_cmd = '/usr/local/bin/git'

    try:
        # Ensure we are in the application root (where .git should be)
        cwd = app.root_path

        # Check if .git exists, if not initialize
        if not os.path.exists(os.path.join(cwd, '.git')):
            subprocess.run([git_cmd, 'init'], cwd=cwd, check=True)
            subprocess.run([git_cmd, 'remote', 'add', 'origin', repo_url], cwd=cwd, check=True)

        # Fetch latest
        subprocess.run([git_cmd, 'fetch', 'origin'], cwd=cwd, check=True)

        # Reset hard to match remote branch
        subprocess.run([git_cmd, 'reset', '--hard', f'origin/{branch}'], cwd=cwd, check=True)

        flash('System updated successfully from GitHub. Service restarting...', 'success')

        # Optional: Restart service if running via systemd (requires sudo/root usually)
        # subprocess.run(['systemctl', 'restart', 'bansos-zivpn'], check=False)

    except FileNotFoundError:
        flash('Update failed: Git is not installed on the server.', 'danger')
    except Exception as e:
        flash(f'Update failed: {str(e)}', 'danger')

    return redirect(url_for('admin_dashboard'))

# --- Server Management Routes ---
@app.route('/admin/servers')
def admin_servers():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    servers = Server.query.all()
    return render_template('admin_servers.html', servers=servers)

@app.route('/admin/server/add', methods=['POST'])
def add_server():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    name = request.form.get('name')
    ip = request.form.get('ip')

    if name and ip:
        token = str(uuid.uuid4())
        new_server = Server(name=name, ip_address=ip, token=token)
        db.session.add(new_server)
        db.session.commit()
        flash(f'Server {name} added. Token: {token}', 'success')

    return redirect(url_for('admin_servers'))

@app.route('/admin/server/delete/<int:id>', methods=['POST'])
def delete_server(id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    server = Server.query.get_or_404(id)
    db.session.delete(server)
    db.session.commit()
    flash('Server deleted.', 'success')
    return redirect(url_for('admin_servers'))

# --- API for Nodes ---
@app.route('/api/node/heartbeat', methods=['POST'])
def node_heartbeat():
    token = request.headers.get('X-Server-Token')
    server = Server.query.filter_by(token=token).first()
    if not server:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    server.last_heartbeat = datetime.datetime.utcnow()
    server.status = 'online'
    server.stats = json.dumps(data) # Expect {cpu: x, ram: y}

    # Update optional info if present
    if 'domain' in data:
        server.domain = data['domain']
    if 'isp' in data:
        server.isp = data['isp']
    if 'country' in data:
        server.country = data['country']

    db.session.commit()
    return jsonify({'status': 'ok'})

@app.route('/api/node/sync', methods=['GET'])
def node_sync():
    token = request.headers.get('X-Server-Token')
    server = Server.query.filter_by(token=token).first()
    if not server:
        return jsonify({'error': 'Unauthorized'}), 401

    # Get active accounts for this server
    accounts = Account.query.filter_by(server_id=server.id, status='active').all()
    account_list = []
    for acc in accounts:
        account_list.append({
            'username': acc.username,
            'password': acc.password,
            'uuid': acc.uuid,
            'protocol': acc.protocol,
            'expiry': acc.expiry_date.isoformat()
        })

    return jsonify({'accounts': account_list})

@app.route('/api/setup/install.sh')
def get_install_script():
    # Dynamic script generation
    host = request.host_url.rstrip('/')
    script = f"""#!/bin/bash
# ZIVPN & Xray Node Installer with Nginx + SSL
# Usage: bash install.sh <token>

TOKEN=$1
MASTER_URL="{host}"

if [ -z "$TOKEN" ]; then
    echo "Error: Token required."
    echo "Usage: bash install.sh <token>"
    exit 1
fi

# Ask for Domain (Argument 2 or Interactive)
NODE_DOMAIN=$2

if [ -z "$NODE_DOMAIN" ]; then
    if [ -t 0 ]; then
        read -p "Enter Domain for this Node (e.g., node1.myserver.com): " NODE_DOMAIN
    else
        if [ -e /dev/tty ]; then
            read -p "Enter Domain for this Node (e.g., node1.myserver.com): " NODE_DOMAIN < /dev/tty
        fi
    fi
fi

if [ -z "$NODE_DOMAIN" ]; then
    echo "Error: Domain is required."
    exit 1
fi

echo "--- BANSOS ZIVPN Node Installer ---"
echo "Master URL: $MASTER_URL"
echo "Token: $TOKEN"
echo "Node Domain: $NODE_DOMAIN"

# 1. Install Dependencies (Include Nginx and Certbot)
apt-get update
apt-get install -y python3 python3-pip curl unzip socat nginx certbot python3-certbot-nginx
pip3 install requests psutil

# 2. Install Xray Core
echo "Installing Xray..."
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install

# 2b. Install ZIVPN (UDP)
echo "Installing ZIVPN..."
mkdir -p /usr/local/etc/zivpn
cd /usr/local/bin
wget -O zivpn https://github.com/zahidbd2/udp-zivpn/raw/main/zivpn
chmod +x zivpn

# Create ZIVPN Service (UDP Port 7200 default)
cat <<EOF > /etc/systemd/system/zivpn.service
[Unit]
Description=ZIVPN UDP Service
After=network.target

[Service]
ExecStart=/usr/local/bin/zivpn --port 7200 --users /usr/local/etc/zivpn/users.json
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable zivpn
systemctl start zivpn

# 3. Configure Nginx (Reverse Proxy for Xray WS)
cat <<EOF > /etc/nginx/sites-available/zivpn
server {{
    listen 80;
    server_name $NODE_DOMAIN;

    location /vmess {{
        proxy_pass http://127.0.0.1:10001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$http_host;
    }}

    location /vless {{
        proxy_pass http://127.0.0.1:10002;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$http_host;
    }}

    location /trojan {{
        proxy_pass http://127.0.0.1:10003;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$http_host;
    }}
}}
EOF

ln -sf /etc/nginx/sites-available/zivpn /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# 4. Obtain SSL Certificate
# Using --non-interactive, might fail if email not provided or other prompts,
# but usually works with --agree-tos.
certbot --nginx -d $NODE_DOMAIN --non-interactive --agree-tos -m admin@$NODE_DOMAIN --redirect

# Reload Nginx to apply changes
systemctl reload nginx

# 5. Create Agent Script
mkdir -p /usr/local/zivpn-agent
cat <<EOF > /usr/local/zivpn-agent/agent.py
import requests
import time
import psutil
import subprocess
import json
import os

MASTER_URL = "$MASTER_URL"
TOKEN = "$TOKEN"
NODE_DOMAIN = "$NODE_DOMAIN"
XRAY_CONFIG_PATH = "/usr/local/etc/xray/config.json"
ZIVPN_USERS_PATH = "/usr/local/etc/zivpn/users.json"

def get_stats():
    return {{
        'cpu': psutil.cpu_percent(),
        'ram': psutil.virtual_memory().percent,
        'domain': NODE_DOMAIN,
    }}

def update_zivpn_config(accounts):
    udp_users = [
        {{'username': acc['username'], 'password': acc['password']}}
        for acc in accounts if acc.get('protocol') == 'udp'
    ]
    try:
        with open(ZIVPN_USERS_PATH, 'w') as f:
            json.dump(udp_users, f, indent=2)
        os.system("systemctl restart zivpn")
    except Exception as e:
        print(f"Error updating ZIVPN: {{e}}")

def update_xray_config(accounts):
    inbounds = []
    # VMESS (10001)
    vmess_users = [
        {{'id': acc['uuid'], 'alterId': 0, 'email': acc['username']}}
        for acc in accounts if acc['protocol'] == 'vmess'
    ]
    if vmess_users:
        inbounds.append({{
            "port": 10001,
            "listen": "127.0.0.1",
            "protocol": "vmess",
            "settings": {{"clients": vmess_users}},
            "streamSettings": {{"network": "ws", "wsSettings": {{"path": "/vmess"}}}}
        }})

    # VLESS (10002)
    vless_users = [
        {{'id': acc['uuid'], 'email': acc['username']}}
        for acc in accounts if acc['protocol'] == 'vless'
    ]
    if vless_users:
        inbounds.append({{
            "port": 10002,
            "listen": "127.0.0.1",
            "protocol": "vless",
            "settings": {{"clients": vless_users, "decryption": "none"}},
            "streamSettings": {{"network": "ws", "wsSettings": {{"path": "/vless"}}}}
        }})

    # TROJAN (10003)
    trojan_users = [
        {{'password': acc['uuid'], 'email': acc['username']}}
        for acc in accounts if acc['protocol'] == 'trojan'
    ]
    if trojan_users:
        inbounds.append({{
            "port": 10003,
            "listen": "127.0.0.1",
            "protocol": "trojan",
            "settings": {{"clients": trojan_users}},
            "streamSettings": {{"network": "ws", "wsSettings": {{"path": "/trojan"}}}}
        }})

    config = {{
        "log": {{"loglevel": "warning"}},
        "inbounds": inbounds,
        "outbounds": [{{"protocol": "freedom"}}]
    }}

    with open(XRAY_CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

    os.system("systemctl restart xray")

def main():
    print("Agent started...")
    while True:
        try:
            stats = get_stats()
            requests.post(f"{{MASTER_URL}}/api/node/heartbeat",
                          json=stats, headers={{'X-Server-Token': TOKEN}}, timeout=10)
            r = requests.get(f"{{MASTER_URL}}/api/node/sync",
                             headers={{'X-Server-Token': TOKEN}}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                accs = data.get('accounts', [])
                update_xray_config(accs)
                update_zivpn_config(accs)
        except Exception as e:
            print(f"Error: {{e}}")
        time.sleep(60)

if __name__ == "__main__":
    main()
EOF

# 6. Install Service
cat <<EOF > /etc/systemd/system/zivpn-agent.service
[Unit]
Description=ZIVPN Node Agent
After=network.target

[Service]
ExecStart=/usr/bin/python3 /usr/local/zivpn-agent/agent.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable zivpn-agent
systemctl start zivpn-agent

echo "Installation Complete! Agent is running."
"""
    return script, 200, {'Content-Type': 'text/plain'}

if __name__ == '__main__':
    if not os.path.exists('database.db'):
        init_db()
    else:
        # Hack to ensure tables exist in dev mode without migration tool
        init_db()

    app.run(debug=True, host='0.0.0.0', port=5000)
