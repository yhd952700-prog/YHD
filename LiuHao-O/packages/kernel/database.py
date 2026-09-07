"""
LIUHAO X - Database Package

SQLAlchemy 2.0 with async support, Alembic migrations.
All models inherit from Base. Schema versioned and auditable.
"""

from sqlalchemy import MetaData, create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Naming convention for consistent constraint names
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(referred_table_name)s_%(referred_column_name)s",
}

class Base(DeclarativeBase):
    """Base class for all LIUHAO X models."""
    pass

# Engines will be created in app startup
engine = None
async_engine = None
SessionLocal = None
async_sessionmaker = None

def init_engine(postgres_dsn: str, redis_dsn: str = "", **kwargs):
    """Initialize database engines."""
    global engine, async_engine, SessionLocal, async_sessionmaker
    
    # Synchronous engine for migrations and sync operations
    engine = create_engine(postgres_dsn, naming_convention=naming_convention, **kwargs)
    
    # Asynchronous engine for app operations
    async_engine = create_async_engine(
        postgres_dsn.replace("postgresql://", "postgresql+asyncpg://"),
        naming_convention=naming_convention,
        echo=False,
        future=True,
    )
    
    # Session makers
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    async_sessionmaker = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False, future=True
    )

def get_engine():
    """Get synchronous engine."""
    return engine

def get_async_engine():
    """Get asynchronous engine."""
    return async_engine

def get_session_local():
    """Get sync session local."""
    return SessionLocal

def get_async_session_local():
    """Get async session local."""
    return async_sessionmaker