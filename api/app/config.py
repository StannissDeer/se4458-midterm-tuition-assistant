"""Runtime configuration, read from environment variables."""
import os


class Settings:
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql+psycopg2://tuition:tuition@localhost:5432/tuition"
    )
    # Shared HS256 secret. The same value is registered as the Kong consumer's JWT credential.
    JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me-dev-secret-at-least-32-bytes!!")
    # Kong's jwt plugin looks up the credential by the `iss` claim, so it must match kong.yml.
    JWT_ISSUER: str = os.getenv("JWT_ISSUER", "tuition-auth")
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
    MOBILE_DAILY_LIMIT: int = int(os.getenv("MOBILE_DAILY_LIMIT", "3"))
    MAX_PAGE_SIZE: int = 100


settings = Settings()
