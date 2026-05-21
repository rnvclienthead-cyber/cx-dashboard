from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Dict, Any
from ..database import get_db

router = APIRouter(
    prefix="/api/v1/claims",
    tags=["Claims / Заявки на модерацию"]
)

class ModerationUpdate(BaseModel):
    updates: Dict[str, Any] 

# УБРАЛИ СЛОВО async! Теперь это обычная функция
@router.get("/pending")
def get_pending_claims(db: Session = Depends(get_db)):
    """
    Получение списка одобренных заявок, ожидающих ручной модерации
    """
    query = """
        SELECT 
            v."SRID", v."Дата и время оформления заявки на возврат", v."Артикул продавца", 
            v."Комментарий покупателя", v."Решение по возврату покупателю", v."Статус товара",
            v."1", v."2", v."3", v."4", v."5", v."6", v."7", v."8", v."9", v."10", v."11", v."12", v."13",
            v."Корректировка", v."Номер поставки",
            COALESCE(inv.invoice_num, 'Не указан') AS "Инвойс"
        FROM view_cx_dashboard v
        LEFT JOIN wb_invoices inv 
            ON TRIM(v."Номер поставки") = inv.supply_id 
            AND TRIM(v."Артикул продавца") = inv.supplier_article
        WHERE (v."1" OR v."2" OR v."3" OR v."4" OR v."5" OR v."6" OR v."7" OR v."8" OR v."9" OR v."10" OR v."11" OR v."12" OR v."13")
          AND (v."Корректировка" IS NULL OR v."Корректировка" = '')
    """
    try:
        # Теперь этот запрос будет выполняться в отдельном потоке (Thread) и не повесит сервер
        result = db.execute(text(query)).mappings().all()
        claims = [dict(row) for row in result]
        
        valid_statuses = {'одобрено', '2', '2.0', 'да', 'true'}
        approved_claims = [
            claim for claim in claims
            if str(claim.get('Решение по возврату покупателю') or '').strip().lower() in valid_statuses or
               str(claim.get('Статус товара') or '').strip().lower() in valid_statuses
        ]
        
        return {"status": "success", "count": len(approved_claims), "data": approved_claims}
        
    except Exception as e:
        print(f"DEBUG SQL Error: {e}") 
        raise HTTPException(status_code=500, detail=f"Ошибка БД: {str(e)}")


# УБРАЛИ СЛОВО async!
@router.post("/{srid}/moderate")
def update_claim_moderation(srid: str, payload: ModerationUpdate, db: Session = Depends(get_db)):
    """
    Сохранение результатов ручной модерации
    """
    if not payload.updates:
        raise HTTPException(status_code=400, detail="Словарь обновлений пуст")
        
    set_clauses = [f"{k} = :{k}" for k in payload.updates.keys()]
    query = text(f"UPDATE wb_claims SET {', '.join(set_clauses)} WHERE srid = :srid")
    
    try:
        params = payload.updates.copy()
        params["srid"] = srid
        
        db.execute(query, params)
        db.commit() 
        
        return {"status": "success", "message": f"Заявка {srid} успешно обновлена"}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при сохранении: {str(e)}")