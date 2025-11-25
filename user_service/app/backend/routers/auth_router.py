from fastapi import APIRouter, HTTPException, status, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from httpx import AsyncClient

from sqlalchemy.orm import Session
from common.db.database import get_db
from ..repositories.user_repository import get_user_by_login, get_user_by_id, create_user, authenticate_user, get_all_users, update_user

from fastapi_redis_session import setSession, getSession, deleteSession
from ..config.jinja_template_config import templates
from common.config.redis_session_config import session_storage
from common.config.services_paths import HOTEL_SERVICE_URL, USER_SERVICE_URL
from common.pydantic.user import UserUpdatePayload

router = APIRouter()


async def link_guest_bookings_service(user_id: int, booking_ids: list):
    """
    Відправляє запит в Hotel Service для прив'язки анонімних бронювань до User ID.
    """
    if not booking_ids or not user_id:
        return

    target_url = f"{HOTEL_SERVICE_URL}/bookings/internal/set-owner"
    payload = {
        "user_id": user_id,
        "booking_ids": booking_ids
    }

    try:
        async with AsyncClient() as client:
            response = await client.post(target_url, json=payload)
            if response.status_code == 200:
                print(f"Success! Linked bookings {booking_ids} to user {user_id}")
                return True
            else:
                print(f"Failed to link bookings. Hotel Service responded: {response.text}")
                return False
    except Exception as e:
        print(f"Error connecting to Hotel Service: {e}")
        return False
async def update_user_phone_service(user_id: int, new_phone: str):
    """Відправляє запит в User Service для оновлення телефону"""
    url = f"{USER_SERVICE_URL}/users/{user_id}"
    payload = {"phone_number": new_phone}
    
    try:
        async with AsyncClient() as client:
            resp = await client.patch(url, json=payload)
            if resp.status_code == 200:
                print(f"User {user_id} phone updated to {new_phone}")
            else:
                print(f"Failed to update phone. User Service: {resp.text}")
    except Exception as e:
        print(f"Error calling User Service: {e}")

@router.get("/registration", response_class=HTMLResponse)
async def register_get(request: Request):
    session = getSession(request, sessionStorage=session_storage)
    if session and session.get("user_id"):
        return RedirectResponse(url=f"{HOTEL_SERVICE_URL}/")
    return templates.TemplateResponse("registration.html", {"request": request, "HOTEL_SERVICE_URL": HOTEL_SERVICE_URL, "USER_SERVICE_URL": USER_SERVICE_URL})

@router.post("/registration", response_class=HTMLResponse)
async def register_post(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    session = getSession(request, sessionStorage=session_storage)
    
    if session and session.get("user_id"):
          return RedirectResponse(url=f"{HOTEL_SERVICE_URL}/", status_code=303)

    user = get_user_by_login(db, login)
    if user:
        return templates.TemplateResponse("registration.html", {"request": request, "error": "Користувач існує", "HOTEL_SERVICE_URL": HOTEL_SERVICE_URL, "USER_SERVICE_URL": USER_SERVICE_URL})
    
    create_user(db, login, password)
    new_user = get_user_by_login(db, login)

    # ЛОГІКА ПРИВ'ЯЗКИ 
    guest_booking_ids = session.get("guest_booking_ids", []) if session else []
    
    if new_user and guest_booking_ids:
        success = await link_guest_bookings_service(new_user.id, guest_booking_ids)
        
        if success and session:
            # 1. Видаляємо ID зі словника в пам'яті
            session.pop("guest_booking_ids", None)
    
            # Отримуємо поточний ID сесії з куки (зазвичай ключ 'ssid', або як у вас в конфігу)
            session_id = request.cookies.get("ssid") 
            
            if session_id:
                # session_storage поводиться як словник (Redis hash/key-value)
                # Цей запис оновлює дані в Redis для цього ключа
                session_storage[session_id] = session
                print(f"Session {session_id} updated in Redis (bookings popped)")

    return templates.TemplateResponse("login.html", {
        "request": request, 
        "msg": "Реєстрація успішна. Бронювання збережено за вашим акаунтом. Увійдіть.", 
        "HOTEL_SERVICE_URL": HOTEL_SERVICE_URL, "USER_SERVICE_URL": USER_SERVICE_URL
    })

@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    session = getSession(request, sessionStorage=session_storage)
    if session and session.get("user_id"):
        return RedirectResponse(url=f"{HOTEL_SERVICE_URL}/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "HOTEL_SERVICE_URL": HOTEL_SERVICE_URL, "USER_SERVICE_URL": USER_SERVICE_URL})

@router.post("/login")
async def login_post(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    # Отримуємо стару (гостьову) сесію
    old_session = getSession(request, sessionStorage=session_storage)
    old_session_id = request.cookies.get("ssid")

    # Якщо користувач вже залогінений - редірект
    if old_session and old_session.get("user_id"):
        return RedirectResponse(url=f"{HOTEL_SERVICE_URL}/", status_code=303)

    user = authenticate_user(db, login, password)

    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Невірний логін або пароль", "HOTEL_SERVICE_URL": HOTEL_SERVICE_URL, "USER_SERVICE_URL": USER_SERVICE_URL})

    # ДОДАТКОВА ПРИВ'ЯЗКА ПРИ ВХОДІ
    # Це покриває випадок, коли користувач наробив бронювань ПІСЛЯ реєстрації, але ДО входу.
    if old_session:
        guest_booking_ids = old_session.get("guest_booking_ids", [])
        if guest_booking_ids:
            print(f"Found orphaned bookings during login: {guest_booking_ids}")
            # Викликаємо ту саму функцію прив'язки
            await link_guest_bookings_service(user.id, guest_booking_ids)
        
        # Очищаємо стару сесію з Redis (Good Practice)
        if old_session_id:
            deleteSession(sessionId=old_session_id, sessionStorage=session_storage)

    # Створюємо нову чисту сесію для авторизованого користувача
    response = RedirectResponse(url=f"{HOTEL_SERVICE_URL}/", status_code=303)
    
    setSession(
        response,
        {"user_id": user.id, "user_role": user.role, "trust_level": user.trust_level},
        sessionStorage=session_storage
    )

    return response
    

@router.get("/users/{user_id}")
async def get_user(
    user_id:int,
    db: Session = Depends(get_db)
):
    user = get_user_by_id(db, user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="User not found"
        )
    
    user_data = {
        "id": user.id,
        "login": user.login,
        "role": user.role,
        "phone_number": user.phone_number,
        "trust_level": user.trust_level
    }

    return user_data

@router.get("/users")
async def get_all_users_list(db: Session = Depends(get_db)):
    users = get_all_users(db)
    
    users_data = [{
        "id": user.id,
        "login": user.login,
        "role": user.role,
        "phone_number": user.phone_number,
        "trust_level": user.trust_level,
        "email": user.email
    } for user in users]
    
    return users_data

@router.patch("/users/{user_id}")
async def update_user_details(
    user_id: int,
    payload: UserUpdatePayload, 
    db: Session = Depends(get_db)
):
    update_data = payload.dict(exclude_unset=True) 

    # 2. Перевірка на порожнечу (якщо клієнт надіслав порожній JSON {})
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No data provided for update"
        )
        
    updated_user = update_user(db, user_id=user_id, update_data=update_data)

    # Обробка результату
    if updated_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="User not found"
        )
    
    return {
        "id": updated_user.id,
        "login": updated_user.login,
        "role": updated_user.role,
        "phone_number": updated_user.phone_number,
        "trust_level": updated_user.trust_level
    }

@router.get("/logout")
async def logout(request: Request):
    session = getSession(request, sessionStorage=session_storage)
    if session:
        deleteSession(sessionId=request.cookies.get("ssid"), sessionStorage=session_storage)
    return RedirectResponse(url=f"{USER_SERVICE_URL}/login")


