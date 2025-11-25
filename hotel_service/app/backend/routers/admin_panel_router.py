from fastapi import APIRouter, Request, Depends, status, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from httpx import AsyncClient
from typing import Optional

from common.db.database import get_db
from common.config.redis_session_config import session_storage
from fastapi_redis_session import getSession
from ..config.jinja_template_config import templates
from common.config.services_paths import HOTEL_SERVICE_URL, USER_SERVICE_URL
from ..repositories import booking_repository
from .booking_router import update_user_data_in_service, get_user_data_from_service

router = APIRouter()

# Допоміжна функція для отримання всіх юзерів із сервісу користувачів
async def get_all_users_from_service() -> list:
    try:
        async with AsyncClient() as client:
            response = await client.get(f"{USER_SERVICE_URL}/users")
            if response.status_code == 200:
                return response.json()
            return []
    except Exception as e:
        print(f"Error fetching users: {e}")
        return []
    
@router.get("/admin/panel", response_class=HTMLResponse)
async def admin_panel(
    request: Request, 
    db: Session = Depends(get_db),
    # Додаємо параметри фільтрації, які приходять з форми (назви мають співпадати з name="" в HTML)
    status_filter: Optional[str] = None,
    phone_filter: Optional[str] = None
):
    session = getSession(request, sessionStorage=session_storage)
    
    if not session or session.get("user_role") != "admin":
        return RedirectResponse(url=f"{HOTEL_SERVICE_URL}/", status_code=status.HTTP_303_SEE_OTHER)

    success_message = session.pop("admin_success", None)
    
    # викликаємо функцію з фільтрами
    # Передаємо значення, отримані з URL
    all_bookings = booking_repository.get_all_bookings_with_filters(
        db, 
        status=status_filter, 
        phone_number=phone_filter
    )
    
    all_users = await get_all_users_from_service()

    context = {
        "request": request,
        "all_bookings": all_bookings,
        "all_users": all_users,
        "is_admin": True,
        "is_authorized": True,
        "success_message": success_message,
        "HOTEL_SERVICE_URL": HOTEL_SERVICE_URL,
        "USER_SERVICE_URL": USER_SERVICE_URL,
        "current_status": status_filter,
        "current_phone": phone_filter
    }
    
    return templates.TemplateResponse("admin_panel.html", context)
# @router.post("/users/trust/{user_id}")
# async def admin_update_user_trust(
#     request: Request,
#     user_id: int,
#     trust_level: int = Form(...), # Отримуємо з <input name="trust_level">
#     db: Session = Depends(get_db)
# ):
#     session = getSession(request, sessionStorage=session_storage)
#     if not session or session.get("user_role") != "admin":
#         raise HTTPException(status_code=403, detail="Access denied")

#     # Відправляємо запит в User Service
#     success = await update_user_data_in_service(user_id, {"trust_level": trust_level})
    
#     if session:
#         if success:
#             session["admin_success"] = f"Рівень довіри користувача (ID: {user_id}) змінено на {trust_level}."
#         else:
#             session["admin_success"] = "Помилка при оновленні даних користувача."

#     return RedirectResponse(url=f"{HOTEL_SERVICE_URL}/admin/panel", status_code=status.HTTP_303_SEE_OTHER)
