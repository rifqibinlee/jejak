"""
Flask application factory.
"""
from flask import Flask
from app import config as _cfg
from agent.memory import init_memory_db


def create_app() -> Flask:
    app = Flask(__name__, template_folder='../templates', static_folder='../static')

    # ── Config ─────────────────────────────────────────────────────────────────
    app.secret_key = _cfg.SECRET_KEY
    app.config['SESSION_COOKIE_HTTPONLY']   = _cfg.SESSION_COOKIE_HTTPONLY
    app.config['SESSION_COOKIE_SAMESITE']   = _cfg.SESSION_COOKIE_SAMESITE
    app.config['PERMANENT_SESSION_LIFETIME'] = _cfg.PERMANENT_SESSION_LIFETIME

    # ── Blueprints ─────────────────────────────────────────────────────────────
    from app.routes.core        import bp as core_bp
    from app.routes.users       import bp as users_bp
    from app.routes.messages    import bp as messages_bp
    from app.routes.reviews     import bp as reviews_bp
    from app.routes.annotations import bp as annotations_bp
    from app.routes.congestion  import bp as congestion_bp
    from app.routes.analytics   import bp as analytics_bp
    from app.routes.pipelines   import bp as pipelines_bp
    from app.routes.rollout     import bp as rollout_bp
    from app.routes.chat        import bp as chat_bp
    from app.routes.misc        import bp as misc_bp

    for bp in (
        core_bp, users_bp, messages_bp, reviews_bp, annotations_bp,
        congestion_bp, analytics_bp, pipelines_bp, rollout_bp, chat_bp, misc_bp,
    ):
        app.register_blueprint(bp)

    # ── Initialise PostgreSQL memory table ─────────────────────────────────────
    init_memory_db()

    return app
