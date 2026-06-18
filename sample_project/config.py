"""App configuration."""

JWT_SECRET = "dev-secret-change-in-prod"
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRY_SECONDS = 3600  # 1 hour

DATABASE_URL = "sqlite:///app.db"
DEBUG = True
