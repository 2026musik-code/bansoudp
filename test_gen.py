from app import app, get_install_script, generate_config_uri, Account, Server

# Create a dummy context for request
with app.test_request_context('/api/setup/install.sh', headers={'Host': 'panel.example.com'}):
    script, status, headers = get_install_script()
    print("--- INSTALL SCRIPT ---")
    print(script)

# Test config URI
dummy_server = Server(name="TestNode", ip_address="1.2.3.4", domain="node1.example.com")
dummy_account = Account(username="user1", protocol="vmess", uuid="1234-5678", server=dummy_server)
uri = generate_config_uri(dummy_account)
print("\n--- VMESS URI ---")
print(uri)

dummy_account.protocol = "vless"
uri = generate_config_uri(dummy_account)
print("\n--- VLESS URI ---")
print(uri)
