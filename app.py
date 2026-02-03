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
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
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
    paymenku_api_key = db.Column(db.String(255), nullable=True)
    price_per_month = db.Column(db.Integer, default=10000)

class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(80), nullable=False) # Storing plain for user display
    ip_address = db.Column(db.String(50), nullable=False)
    pin = db.Column(db.String(10), unique=True, nullable=False) # The PIN needed to access
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    expiry_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='pending') # pending, active, expired

    # Paymenku specific
    reference_id = db.Column(db.String(100), unique=True, nullable=True)
    trx_id = db.Column(db.String(100), nullable=True)

# --- Helper Functions ---
def get_system_stats():
    cpu_percent = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    ram_percent = ram.percent

    # Network Stats
    net = psutil.net_io_counters()
    traffic_sent = round(net.bytes_sent / (1024 * 1024), 2) # MB
    traffic_recv = round(net.bytes_recv / (1024 * 1024), 2) # MB

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
        'domain': request.host if request else 'localhost',
        'traffic_sent': traffic_sent,
        'traffic_recv': traffic_recv
    }

def init_db():
    with app.app_context():
        db.create_all()
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
            # Assuming the response is a list of channels or wrapped in data
            # Based on image 2, it looks like a list or table.
            # Usually APIs return { success: true, data: [...] } or just [...]
            # We'll assume direct list or data key.
            # If parsing fails, return defaults.
            return data if isinstance(data, list) else data.get('data', [])
    except:
        pass
    return []

# --- User Routes ---
@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    stats = get_system_stats()
    settings = Settings.query.first()
    channels = []
    if settings and settings.paymenku_api_key:
        channels = get_paymenku_channels(settings.paymenku_api_key)

    # Fallback if API fails or no key, just so UI shows something
    if not channels:
        channels = [
            {'code': 'qris', 'name': 'QRIS', 'type': 'qris'},
            {'code': 'bca_va', 'name': 'BCA Virtual Account', 'type': 'va'},
            {'code': 'dana', 'name': 'DANA', 'type': 'ewallet'}
        ]

    return render_template('dashboard.html', stats=stats, settings=settings, channels=channels)

@app.route('/buy', methods=['POST'])
def buy():
    username = request.form.get('username')
    password = request.form.get('password')
    channel_code = request.form.get('channel_code')

    if not username or not password:
        flash('Username dan Password wajib diisi!', 'danger')
        return redirect(url_for('dashboard'))

    if Account.query.filter_by(username=username).first():
        flash('Username sudah digunakan. Pilih yang lain.', 'warning')
        return redirect(url_for('dashboard'))

    settings = Settings.query.first()
    if not settings or not settings.paymenku_api_key:
        flash('Sistem pembayaran belum dikonfigurasi admin.', 'danger')
        return redirect(url_for('dashboard'))

    stats = get_system_stats()
    server_ip = stats['ip']

    # Generate PIN
    pin = str(random.randint(100000, 999999))
    while Account.query.filter_by(pin=pin).first():
        pin = str(random.randint(100000, 999999))

    # Reference ID for Paymenku
    reference_id = f"INV-{int(datetime.datetime.utcnow().timestamp())}-{random.randint(100,999)}"

    # Create Pending Account
    expiry = datetime.datetime.utcnow() + datetime.timedelta(days=30)

    new_account = Account(
        username=username,
        password=password,
        ip_address=server_ip,
        pin=pin,
        expiry_date=expiry,
        status='pending',
        reference_id=reference_id
    )

    db.session.add(new_account)
    db.session.commit()

    # Call Paymenku API
    payload = {
        "reference_id": reference_id,
        "amount": settings.price_per_month,
        "customer_name": username,
        "customer_email": "user@zivpn.local", # Placeholder as we don't ask email
        "customer_phone": "08123456789", # Placeholder
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

        # We expect a success response with a payment URL or QR string
        # Typically: { success: true, data: { payment_url: "...", ... } }
        # Or direct fields. Based on typical structure.

        if r.status_code == 200 and resp_data.get('success', True): # permissive check
            # Look for payment_url or similar
            data = resp_data.get('data', resp_data)
            payment_url = data.get('payment_url') or data.get('redirect_url')

            if payment_url:
                return redirect(payment_url)
            else:
                # If no URL, maybe it returns raw QR?
                # For now assume redirect.
                flash(f"Error getting payment URL: {resp_data}", 'danger')
        else:
            flash(f"Payment Gateway Error: {resp_data.get('message', r.text)}", 'danger')

    except Exception as e:
        flash(f"Connection Error: {str(e)}", 'danger')

    # If failed, delete the pending account so they can try again with same username
    db.session.delete(new_account)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/callback', methods=['POST'])
def payment_callback():
    # Payload: { "event": "payment.status_updated", "trx_id": "...", "reference_id": "...", "status": "paid", ... }
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
    # If users hit return_url but callback hasn't fired yet, we might want to check status manually
    if account.status != 'active':
        # Optional: Call check-status API here if strictly needed
        flash('Pembayaran sedang diproses. Tunggu sebentar atau refresh.', 'info')

    return render_template('success.html', pin=account.pin, account=account)

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
                account = None

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

    try:
        if os.path.exists('.git'):
            subprocess.run(['git', 'pull'], check=True)
            flash('System updated from GitHub. Restarting service...', 'success')
        else:
             flash('Git repository not found. Cannot update.', 'warning')
    except Exception as e:
        flash(f'Update failed: {str(e)}', 'danger')

    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    if not os.path.exists('database.db'):
        init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
