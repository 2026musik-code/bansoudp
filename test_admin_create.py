import unittest
from app import app, db, Admin, Account, Server
from flask import session

class AdminCreateTestCase(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app = app.test_client()
        with app.app_context():
            db.create_all()
            # Check if admin exists before creating
            if not Admin.query.filter_by(username='admin').first():
                admin = Admin(username='admin')
                admin.set_password('admin')
                db.session.add(admin)
            # Check if server exists
            if not Server.query.filter_by(name="TestServer").first():
                server = Server(name="TestServer", ip_address="1.2.3.4", token="token123", status="online")
                db.session.add(server)
            db.session.commit()

    def login(self):
        with self.app.session_transaction() as sess:
            sess['admin_logged_in'] = True

    def test_create_account(self):
        self.login()
        with app.app_context():
            server = Server.query.first()
            response = self.app.post('/admin/create_account', data=dict(
                username='newuser',
                password='password123',
                protocol='udp',
                server_id=server.id,
                duration=30
            ), follow_redirects=True)

            self.assertEqual(response.status_code, 200)
            account = Account.query.filter_by(username='newuser').first()
            self.assertIsNotNone(account)
            self.assertEqual(account.status, 'active')
            self.assertEqual(account.protocol, 'udp')

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

if __name__ == '__main__':
    unittest.main()
