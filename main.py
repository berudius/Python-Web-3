from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from user_service.app.backend.routers import auth_router
from common.db.database import Base, engine
from hotel_service.app.backend.routers import  public_router, services_router, about_us_router, rooms_router, booking_router, admin_panel_router
from hotel_service.app.backend.config.statica_config import static_dir_path
# from common.docker.redis_launcher import run_redis, stop_redis
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
app.mount("/static", StaticFiles(directory=static_dir_path), name="static")

app.include_router(auth_router.router)

app.include_router(public_router.router)
app.include_router(services_router.router)
app.include_router(about_us_router.router)
app.include_router(rooms_router.router)
app.include_router(booking_router.router)
app.include_router(admin_panel_router.router)

