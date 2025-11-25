from fastapi import APIRouter, Request, Depends, status, Query, HTTPException
from sqlalchemy.orm import Session
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi_redis_session import getSession, setSession  # <-- Додано setSession
from httpx import AsyncClient
from datetime import datetime, timedelta
from urllib.parse import urlencode



from ..config.jinja_template_config import templates
from common.config.redis_session_config import session_storage
from common.db.database import get_db
from common.config.services_paths import USER_SERVICE_URL, HOTEL_SERVICE_URL
from ..repositories import booking_repository, room_repository

from typing import List, Optional, Dict
from pydantic import BaseModel
import urllib.parse

router = APIRouter()

class CreateBookingPayload(BaseModel):
    physical_room_ids: List[int]
    arrival_date: datetime
    departure_date: datetime
    phone_number: str
    book_without_confirmation: bool = False
    save_phone: bool = False
    update_profile_phone: bool = False

class UpdateBookingStatusPayload(BaseModel):
    status: str

# Модель даних, які прийдуть від Auth Service
class LinkUserPayload(BaseModel):
    user_id: int
    booking_ids: List[int]

async def get_user_data_from_service(user_id: int) -> Optional[dict]:
    try:
        async with AsyncClient() as client:
            response = await client.get(f"{USER_SERVICE_URL}/users/{user_id}")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return None

async def update_user_data_in_service(user_id: int, updates: dict) -> bool:
    try:
        async with AsyncClient() as client:
            response = await client.patch(f"{USER_SERVICE_URL}/users/{user_id}", json=updates)
            response.raise_for_status()
            return True
    except Exception as e:
        return False

# def generate_auth_urls(base_url: str, redirect_path: str, booking_ids: List[int]) -> dict:
#     params = {"redirect_url": redirect_path}
#     if booking_ids:
#         params["guest_bookings"] = ",".join(map(str, booking_ids))
#     encoded_params = urlencode(params)
#     return {
#         "login_url": f"{base_url}/login",
#         "register_url": f"{base_url}/registration?{encoded_params}"
#     }

async def update_user_phone_service(user_id: int, new_phone: str):
    """Відправляє запит в User Service для оновлення телефону"""
    url = f"{USER_SERVICE_URL}/users/{user_id}"
    payload = {"phone_number": new_phone}
    
    try:
        async with AsyncClient() as client:
            # Використовуємо PATCH, який ви вже реалізували в User Service
            resp = await client.patch(url, json=payload)
            if resp.status_code == 200:
                print(f"User {user_id} phone updated to {new_phone}")
            else:
                print(f"Failed to update phone. User Service: {resp.text}")
    except Exception as e:
        print(f"Error calling User Service: {e}")


@router.patch("/admin/bookings/{booking_id}/status")
async def update_booking_status_by_admin(
    request: Request,  # <--- Додано об'єкт Request для доступу до сесії
    booking_id: int,
    payload: UpdateBookingStatusPayload,
    db: Session = Depends(get_db)
):
    # === 1. ПЕРЕВІРКА БЕЗПЕКИ (SECURITY CHECK) ===
    session = getSession(request, sessionStorage=session_storage)
    
    # Якщо сесії немає АБО роль не адмін -> Помилка 403
    if not session or session.get("user_role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Доступ заборонено. Потрібні права адміністратора."
        )

    # === 2. ОТРИМАННЯ БРОНЮВАННЯ ===
    booking_to_update = booking_repository.get_booking_by_id(db, booking_id)
    if not booking_to_update:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Бронювання не знайдено.")

    old_status = booking_to_update.status
    
    # Оновлюємо статус в БД
    booking_repository.update_booking_status(db, booking_id, payload.status)
    
    user_id = booking_to_update.user_id
    
    # Якщо це гостьове бронювання або статус не змінився на "Завершено" - виходимо
    if not user_id or payload.status != "Завершено" or old_status == "Завершено":
        return JSONResponse(content={"success": True, "message": "Статус оновлено."})

    # === 3. ЛОГІКА ДОВІРИ (GAMIFICATION) ===
    
    user_data = await get_user_data_from_service(user_id)
    if not user_data:
        # Повертаємо успіх, бо статус бронювання ми все-таки змінили, 
        # але повідомляємо про проблему з User Service
        return JSONResponse(
            status_code=status.HTTP_200_OK, 
            content={"success": True, "message": "Статус оновлено, але не вдалося зв'язатися з User Service для оновлення рейтингу."}
        )

    current_trust_level = user_data.get("trust_level", 0)
    current_cancellations = user_data.get("consecutive_cancellations", 0)
    
    completed_count = booking_repository.count_bookings_by_status(db, user_id, "Завершено")

    # Розрахунок потенціалу (на що заслуговує історія)
    potential_level = 0
    if completed_count >= 10: potential_level = 3
    elif completed_count >= 5: potential_level = 2
    elif completed_count >= 2: potential_level = 1

    updates = {}
    penalty_cleared = False

    # Крок А: Спочатку знімаємо штрафи (якщо є)
    if current_cancellations > 0:
        updates["consecutive_cancellations"] = 0
        penalty_cleared = True
    
    # Крок Б: Зростання рівня (тільки якщо зайшли "чистими", без штрафів)
    else:
        if potential_level > current_trust_level:
            # Реалізуємо "Сходи": +1 рівень за раз
            new_level = current_trust_level + 1
            updates["trust_level"] = new_level

    # === 4. ВІДПРАВКА ОНОВЛЕНЬ ===
    if updates:
        await update_user_data_in_service(user_id, updates)
        
        msg = "Статус оновлено."
        if penalty_cleared:
            msg += " З користувача знято штрафні бали за скасування (рівень не змінено)."
        elif "trust_level" in updates:
            msg += f" Рівень довіри підвищено до {updates['trust_level']}."
            
        return JSONResponse(content={"success": True, "message": msg})

    return JSONResponse(content={"success": True, "message": "Статус оновлено. Рівень довіри без змін."})



@router.get("/auth/sync")
async def sync_guest_bookings(
    request: Request,
    guest_bookings: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    session = getSession(request=request, sessionStorage=session_storage)
    user_id = session.get("user_id") if session else None

    if not user_id:
        return RedirectResponse(url=f"{USER_SERVICE_URL}/login")

    if guest_bookings:
        try:
            booking_ids_to_sync = [int(id_str) for id_str in guest_bookings.split(',')]
            updated_count = booking_repository.associate_bookings_to_user_by_ids(db, booking_ids_to_sync, user_id)
            if updated_count > 0:
                # Додано перевірку session перед .set()
                if session:
                    session.set("booking_success", f"Ваші гостьові бронювання (кількість: {updated_count}) було успішно прив\'язано до акаунту.")
        except (ValueError, TypeError):
            pass
    
    # Додано перевірку session перед .pop()
    if session:
        session.pop("guest_booking_ids", None)
    return RedirectResponse(url=f"{HOTEL_SERVICE_URL}/", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/bookings/create_json")
async def create_booking_json(request: Request, payload: CreateBookingPayload, db: Session = Depends(get_db)):
    session = getSession(request=request, sessionStorage=session_storage)
    user_id = None
    guest_bookings = []

    if session:
        user_id = session.get("user_id")
        # Додаткова перевірка рівня довіри із сесії для безпеки
        user_trust_level = session.get("trust_level", 0)
        
        if not user_id:
            guest_bookings = session.get("guest_booking_ids", [])

    # === ВАЛІДАЦІЯ ДАТ ===
    print(f"HERE arrival_date: {payload.arrival_date}")
    print(f"HERE departure_date: {payload.departure_date}")
    if payload.arrival_date < datetime.now() + timedelta(minutes=80):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"success": False, "message": "Час прибуття має бути щонайменше на 1 годину 20 хвилин пізніше за поточний час."}
        )
    
    if (payload.departure_date - payload.arrival_date).total_seconds() < 86400:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"success": False, "message": "Мінімальний термін — 24 години."}
        )

    # === ПЕРЕВІРКА ДОСТУПНОСТІ ===
    if not booking_repository.are_rooms_available(db, payload.physical_room_ids, payload.arrival_date, payload.departure_date):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"success": False, "message": "На жаль, вибрані номери вже зайняті на цей час. "}
        )

    # === ЗМІНА 1: ЛОГІКА СТАТУСУ ===
    # Якщо користувач просив не дзвонити, ставимо "Підтверджено" (або "Нове", залежно від вашої бізнес-логіки)
    # Важливо: бажано перевіряти trust_level ще раз тут, щоб хакери не слали book_without_confirmation вручну
    booking_status = "Розглядається" # Default (Pending)
    if user_id and user_trust_level >=2 and payload.book_without_confirmation:
         booking_status = "Підтверджено" # Confirmed

    try:
        new_booking = booking_repository.add_booking(
            db=db, 
            phone_number=payload.phone_number,
            physical_room_ids=payload.physical_room_ids,  
            arrival_date=payload.arrival_date, 
            departure_date=payload.departure_date,
            status=booking_status,
            user_id=user_id
        )
        
        # === ЗМІНА 2: ОНОВЛЕННЯ ТЕЛЕФОНУ ===
        # Якщо користувач залогінений І поставив галочку "Зберегти/Замінити телефон"
        should_update_phone = payload.save_phone or payload.update_profile_phone
        if user_id and should_update_phone:
                await update_user_phone_service(user_id, payload.phone_number)

    except Exception as e:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"success": False, "message": str(e)})

    message = "Бронювання успішно створено!"
    if booking_status == "Підтверджено":
        message = "Бронювання успішно підтверджено! Чекаємо на вас."

    response = JSONResponse(content={"success": True, "message": message})

    # === ЛОГІКА ДЛЯ ГОСТЕЙ (Сесія) ===
    if not user_id:
        if new_booking.id not in guest_bookings:
            guest_bookings.append(new_booking.id)
        
        setSession(
            response,
            {"guest_booking_ids": guest_bookings, "phone_number": payload.phone_number},
            sessionStorage=session_storage
        )

    return response

@router.get("/bookings")
async def get_booking_confirmation_page(
    request: Request,
    db: Session = Depends(get_db),
    # ЗМІНА 4: Очікуємо параметр ?physical_room_ids=1&physical_room_ids=2
    physical_room_ids: Optional[List[int]] = Query(None), 
    arrival_date: Optional[datetime] = Query(None),
    departure_date: Optional[datetime] = Query(None)
):
    session = getSession(request=request, sessionStorage=session_storage)
    print(f"arrival_date: {arrival_date}")

    if not physical_room_ids or not arrival_date or not departure_date:
        return RedirectResponse(url=f"{HOTEL_SERVICE_URL}/rooms", status_code=status.HTTP_303_SEE_OTHER)
    
    if arrival_date < datetime.now() + timedelta(minutes=80):
        error_text = "Час прибуття має бути щонайменше на 1 годину 20 хвилин пізніше за поточний час."
        encoded_error = urllib.parse.quote(error_text)
        return RedirectResponse(
            url=f"{HOTEL_SERVICE_URL}/rooms?error_message={encoded_error}", 
            status_code=status.HTTP_303_SEE_OTHER
        )
    
    if (departure_date - arrival_date).total_seconds() < 86400:
        error_text = "Мінімальний часовий проміжок бронювання — 24 години (одна доба)."
        # Кодуємо текст для URL (щоб пробіли стали %20 і т.д.)
        encoded_error = urllib.parse.quote(error_text)
        
        # Передаємо помилку прямо в URL
        return RedirectResponse(
            url=f"{HOTEL_SERVICE_URL}/rooms?error_message={encoded_error}", 
            status_code=status.HTTP_303_SEE_OTHER
        )
    # =========================================

    if not booking_repository.are_rooms_available(db, physical_room_ids, arrival_date, departure_date):
        error_text ="На жаль, вибрані номери вже зайняті на цей час. "
        encoded_error = urllib.parse.quote(error_text)
        return RedirectResponse(
            url=f"{HOTEL_SERVICE_URL}/rooms?error_message={encoded_error}", 
            status_code=status.HTTP_303_SEE_OTHER
        )

    selected_physical_rooms = room_repository.get_physical_rooms_with_parents(db, physical_room_ids)
    
    total_price_per_night = sum(pr.room_model.price for pr in selected_physical_rooms)
    nights = (departure_date - arrival_date).days
    if nights < 1: nights = 1
    full_price = total_price_per_night * nights

    user_id = session.get("user_id") if session else None
    is_authorized = bool(user_id)
    guest_booking_ids = session.get("guest_booking_ids", []) if session else []
    last_guest_phone = session.get("phone_number", "") if session else ""
    
    user_phone = ""
    trust_level = 0
    if is_authorized:
         user_data = await get_user_data_from_service(user_id)
         if user_data:
             user_phone = user_data.get("phone_number", "")
             trust_level = user_data.get("trust_level", 0)

    # auth_urls = generate_auth_urls(USER_SERVICE_URL, f"{HOTEL_SERVICE_URL}/auth/sync", guest_booking_ids)

    context = {
        "request": request,
        "selected_rooms": selected_physical_rooms,
        "physical_room_ids": physical_room_ids, # ЗМІНА 5: Передаємо правильну назву в шаблон
        "arrival_date": arrival_date,     
        "departure_date": departure_date,
        "total_price": full_price,
        "price_per_night": total_price_per_night,
        "nights": nights,
        "is_authorized": is_authorized,
        "phone_number": user_phone or last_guest_phone,
        "trust_level": trust_level,
        "guest_booking_ids": guest_booking_ids,
        "USER_SERVICE_URL": USER_SERVICE_URL,
        "HOTEL_SERVICE_URL": HOTEL_SERVICE_URL
    }
    
    return templates.TemplateResponse("booking.html", context)


# Внутрішній ендпоінт (приймає дані від Auth Service)
@router.post("/bookings/internal/set-owner")
async def set_booking_owner(
    payload: LinkUserPayload, 
    db: Session = Depends(get_db)
):
    if not payload.booking_ids:
        return JSONResponse(content={"success": False, "message": "Список бронювань порожній"})

    # Викликаємо створений вами метод репозиторію
    count = booking_repository.update_bookings_with_user_id(
        db, 
        payload.booking_ids, 
        payload.user_id
    )
    
    return JSONResponse(content={"success": True, "updated_count": count})


