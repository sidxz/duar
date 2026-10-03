"""Create or promote a user to admin."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))

from src.models.user import User

DATABASE_URL = "postgresql+asyncpg://identity:identity_dev@localhost:9001/identity"


async def create_admin(email: str):
    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user:
            if user.is_admin:
                print(f"'{email}' is already an admin.")
            else:
                user.is_admin = True
                await db.commit()
                print(f"Promoted '{email}' to admin.")
        else:
            user = User(email=email, name=email.split("@")[0], is_admin=True)
            db.add(user)
            await db.commit()
            print(f"Created admin user '{email}' (will be linked on first OAuth login).")

    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        email = sys.argv[1]
    else:
        email = input("Admin email: ").strip()
    if not email or "@" not in email:
        print("Invalid email.")
        sys.exit(1)
    asyncio.run(create_admin(email))
