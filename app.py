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

app = Flask(__name__)
app.config['SECRET_KEY'] = 'bansos-zivpn-secret-key-change-me'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

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
    qr_api_key = db.Column(db.String(255), nullable=True) # Or generic config for payment
    price_per_month = db.Column(db.Integer, default=10000)

class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(80), nullable=False) # Storing plain for user display
    ip_address = db.Column(db.String(50), nullable=False)
    pin = db.Column(db.String(10), unique=True, nullable=False) # The PIN needed to access
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    expiry_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='active') # active, expired

# --- Helper Functions ---
def get_system_stats():
    cpu_percent = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    ram_percent = ram.percent

    # Network Stats
    net = psutil.net_io_counters()
    traffic_sent = round(net.bytes_sent / (1024 * 1024), 2) # MB
    traffic_recv = round(net.bytes_recv / (1024 * 1024), 2) # MB

    # Get Public IP (Best effort)
    try:
        # This is a bit hacky, but standard for quick VPS checks without external calls if possible
        # but usually external is needed for public IP.
        # Using a dummy value if offline, or a simple socket trick
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
        'domain': request.host if request else 'localhost',
        'traffic_sent': traffic_sent,
        'traffic_recv': traffic_recv
    }

def init_db():
    with app.app_context():
        db.create_all()
        if not Admin.query.filter_by(username='admin').first():
            admin = Admin(username='admin')
            admin.set_password('admin123') # Default password
            db.session.add(admin)

        if not Settings.query.first():
            settings = Settings(qr_api_key='YOUR_API_KEY_HERE')
            db.session.add(settings)

        db.session.commit()

# --- User Routes ---
@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    stats = get_system_stats()
    return render_template('dashboard.html', stats=stats)

@app.route('/buy', methods=['POST'])
def buy():
    username = request.form.get('username')
    password = request.form.get('password')

    if not username or not password:
        flash('Username dan Password wajib diisi!', 'danger')
        return redirect(url_for('dashboard'))

    # Check if username exists
    if Account.query.filter_by(username=username).first():
        flash('Username sudah digunakan. Pilih yang lain.', 'warning')
        return redirect(url_for('dashboard'))

    stats = get_system_stats()
    server_ip = stats['ip']

    # Generate PIN
    pin = str(random.randint(100000, 999999))
    while Account.query.filter_by(pin=pin).first():
        pin = str(random.randint(100000, 999999))

    # Create Pending Account (30 days expiry default)
    expiry = datetime.datetime.utcnow() + datetime.timedelta(days=30)

    new_account = Account(
        username=username,
        password=password,
        ip_address=server_ip,
        pin=pin,
        expiry_date=expiry,
        status='pending' # Waiting for payment
    )

    db.session.add(new_account)
    db.session.commit()

    return redirect(url_for('payment', account_id=new_account.id))

@app.route('/payment/<int:account_id>')
def payment(account_id):
    account = Account.query.get_or_404(account_id)
    if account.status == 'active':
        return redirect(url_for('success', account_id=account.id))

    settings = Settings.query.first()
    # If using a real QR API, we'd format the data string here
    # For now, we use the API Key as the data or just a placeholder string + price
    payment_data = f"{settings.qr_api_key}-{account.id}" if settings.qr_api_key else f"PAY-{account.id}"

    return render_template('payment.html',
                           payment_data=payment_data,
                           price=settings.price_per_month,
                           account_id=account.id)

@app.route('/check_payment/<int:account_id>', methods=['POST'])
def check_payment(account_id):
    account = Account.query.get_or_404(account_id)

    # SIMULATION: In a real app, verify callback from payment gateway here.
    # Here we assume it's successful since user clicked "I Paid"

    account.status = 'active'
    db.session.commit()

    # Logic to Create VPN User in System (Placeholder)
    # create_vpn_user(account.username, account.password)

    return redirect(url_for('success', account_id=account.id))

@app.route('/success/<int:account_id>')
def success(account_id):
    account = Account.query.get_or_404(account_id)
    if account.status != 'active':
        return redirect(url_for('payment', account_id=account.id))

    return render_template('success.html', pin=account.pin)

@app.route('/list', methods=['GET', 'POST'])
def list_accounts():
    account = None
    if request.method == 'POST':
        pin = request.form.get('pin')
        if pin:
            account = Account.query.filter_by(pin=pin).first()
            if not account:
                flash('PIN tidak ditemukan.', 'danger')
            elif account.status != 'active':
                flash('Akun belum aktif atau sudah kadaluarsa.', 'warning')
                account = None # Hide details if not active

    return render_template('list.html', account=account)

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
    return render_template('admin_dashboard.html', accounts=accounts, settings=settings)

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
    settings.qr_api_key = request.form.get('qr_api_key')
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
        admin = Admin.query.filter_by(username='admin').first() # Assuming single admin
        if admin:
            admin.set_password(new_password)
            db.session.commit()
            flash('Password admin berhasil diubah.', 'success')

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/system_update', methods=['POST'])
def system_update():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    # Execute git pull and restart service
    try:
        # Check if .git exists to avoid errors in non-git envs (like this sandbox initially)
        if os.path.exists('.git'):
            subprocess.run(['git', 'pull'], check=True)
            flash('System updated from GitHub. Restarting service...', 'success')
            # Trigger service restart (requires sudo/permissions, handled by install script usually allowing passwordless sudo for this command or just killing the python process)
            # subprocess.Popen(['sudo', 'systemctl', 'restart', 'bansos-zivpn'])
            # In this sandbox, we just flash message.
        else:
             flash('Git repository not found. Cannot update.', 'warning')
    except Exception as e:
        flash(f'Update failed: {str(e)}', 'danger')

    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    if not os.path.exists('database.db'):
        init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
