from fastapi import FastAPI
from user_service.app.backend.routers import auth_router
from common.db.database import Base, engine
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # run_redis()
    
    try:
        yield
    except Exception as ex:
        print(ex)
    # finally:
    #     stop_redis()

app = FastAPI(lifespan=lifespan)

app.include_router(auth_router.router)

