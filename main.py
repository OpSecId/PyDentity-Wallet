import os
import ngrok
from dotenv import load_dotenv
from qrcode import QRCode

from app import create_app

app = create_app()

if __name__ == "__main__":
    load_dotenv()

    # Only start ngrok in the reloader child to avoid duplicate tunnels
    if os.environ.get('WERKZEUG_RUN_MAIN'):
        if os.getenv("NGROK_AUTHTOKEN", None):
            try:
                listener = ngrok.forward(
                    5000,
                    authtoken=os.getenv("NGROK_AUTHTOKEN"),
                    domain=os.getenv("PYDENTITY_WALLET_DOMAIN")
                )
                # Store ngrok URL in app config
                app.config['NGROK_URL'] = listener.url()
                
                qr = QRCode(box_size=10, border=4)
                qr.add_data(listener.url())
                qr.print_ascii()
            except ValueError:
                pass
        
    app.run(host="0.0.0.0", port="5000", debug=True, use_reloader=True)
