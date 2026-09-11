"""
LIUHAO X - Database Package

SQLAlchemy 2.0 with async support, Alembic migrations.
All models inherit from Base. Schema versioned and auditable.
"""

from sqlalchemy import MetaData, create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Naming convention for consistent constraint names.
#
# 归属纠正（F-06）：naming_convention 是 **MetaData** 的参数，不是 Engine 的参数。
# 原实现把它传给 create_engine()/create_async_engine()，SQLAlchemy 2.0 会立刻抛
#   TypeError: Invalid argument(s) 'naming_convention' sent to create_engine()
# 也就是说 init_engine() 从来没被真正调用成功过 —— 因为没有任何调用方，
# 这个 bug 一直没暴露。现在挂在 Base.metadata 上，Alembic 生成约束名时才生效。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(referred_table_name)s_%(referred_column_name)s",
}

# 未显式指定 driver 时，同步 DSN -> 对应异步 driver 的映射。
# 已带 "+driver" 的 DSN 原样保留（例如 postgresql+psycopg:// 不该被改写）。
_ASYNC_DRIVERS = {
    "postgresql": "postgresql+asyncpg",
    "mysql": "mysql+aiomysql",
    "sqlite": "sqlite+aiosqlite",
}


def to_async_dsn(dsn: str) -> str:
    """把同步 DSN 转成异步 DSN；已指定 driver 或未知 scheme 则原样返回。"""
    scheme = dsn.split("://", 1)[0]
    if "+" in scheme:
        return dsn
    if scheme in _ASYNC_DRIVERS:
        return _ASYNC_DRIVERS[scheme] + dsn[len(scheme):]
    return dsn


class Base(DeclarativeBase):
    """Base class for all LIUHAO X models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Engines will be created in app startup
engine = None
async_engine = None
SessionLocal = None
# 注意：不能用 `async_sessionmaker` 作模块变量名，会遮蔽同名导入。
async_session_maker = None


def init_engine(postgres_dsn: str, redis_dsn: str = "", **kwargs):
    """Initialize database engines.

    参数名保留 postgres_dsn 以维持既有调用约定，但实现不再绑定 PostgreSQL：
    任意 SQLAlchemy DSN 都可用（便于用 sqlite+aiosqlite 做回归测试）。
    **kwargs 只传给同步 engine —— 异步 engine 的 pool 等参数并不通用。
    """
    global engine, async_engine, SessionLocal, async_session_maker

    # Synchronous engine for migrations and sync operations
    engine = create_engine(postgres_dsn, **kwargs)

    # Asynchronous engine for app operations
    async_engine = create_async_engine(to_async_dsn(postgres_dsn), echo=False)

    # Session makers
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    # 用 async_sessionmaker 而非 sessionmaker(class_=AsyncSession, future=True)：
    # future 是 1.4 遗留开关，2.0 的 Session 已无此语义。
    async_session_maker = async_sessionmaker(async_engine, expire_on_commit=False)


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
    """Get async session factory (call it to obtain an AsyncSession)."""
    return async_session_maker
