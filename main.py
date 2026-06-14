"""
Application entry point.
Run with:  python main.py
           gunicorn -w 2 -b 0.0.0.0:5000 main:app
"""
from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(
        debug=True,
        host='0.0.0.0',
        port=5000,
    )
