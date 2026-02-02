#!/bin/bash

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo "Harap jalankan script ini sebagai root (sudo)."
  exit
fi

echo "==========================================="
echo "   INSTALLER BANSOS ZIVPN WEB PANEL"
echo "==========================================="

# Ask for Domain
read -p "Masukkan Domain Anda (contoh: panel.domain.com): " DOMAIN

if [ -z "$DOMAIN" ]; then
    echo "Domain tidak boleh kosong!"
    exit 1
fi

echo "Updating Package Lists..."
apt update

echo "Installing Dependencies..."
apt install -y python3-pip python3-venv nginx git

# Setup Project Directory
TARGET_DIR="/var/www/bansos-zivpn"

echo "Setting up Directory at $TARGET_DIR..."
# Create directory if not exists
mkdir -p $TARGET_DIR

# Copy application files
# We assume the script is run from the source directory
cp app.py $TARGET_DIR/
cp requirements.txt $TARGET_DIR/
cp -r templates $TARGET_DIR/
cp -r static $TARGET_DIR/

cd $TARGET_DIR

# Setup Python Venv
echo "Setting up Python Environment..."
# Check if venv exists, if not create it
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install -r requirements.txt

# Initialize Database
echo "Initializing Database..."
python3 -c "from app import init_db; init_db()"

# Fix permissions (Must be done after DB init to ensure www-data owns the db file)
chown -R www-data:www-data $TARGET_DIR
chmod -R 755 $TARGET_DIR

# Create Systemd Service
echo "Creating Systemd Service..."
cat > /etc/systemd/system/bansos-zivpn.service <<EOL
[Unit]
Description=Gunicorn instance to serve BANSOS ZIVPN
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=$TARGET_DIR
Environment="PATH=$TARGET_DIR/venv/bin"
ExecStart=$TARGET_DIR/venv/bin/gunicorn --workers 3 --bind unix:bansos-zivpn.sock -m 007 app:app

[Install]
WantedBy=multi-user.target
EOL

# Start Service
systemctl daemon-reload
systemctl start bansos-zivpn
systemctl enable bansos-zivpn

# Configure Nginx
echo "Configuring Nginx..."
cat > /etc/nginx/sites-available/bansos-zivpn <<EOL
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        include proxy_params;
        proxy_pass http://unix:$TARGET_DIR/bansos-zivpn.sock;
    }
}
EOL

# Enable Nginx Site
ln -sf /etc/nginx/sites-available/bansos-zivpn /etc/nginx/sites-enabled/
# Only remove default if it exists
if [ -f /etc/nginx/sites-enabled/default ]; then
    rm /etc/nginx/sites-enabled/default
fi

nginx -t
systemctl restart nginx

echo "==========================================="
echo "   INSTALASI SELESAI!"
echo "==========================================="
echo "Web Panel bisa diakses di: http://$DOMAIN"
echo "Admin Login: http://$DOMAIN/admin/login"
echo "Default Admin: admin / admin123"
echo "==========================================="
