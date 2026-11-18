from fastapi import APIRouter,Depends
from .database import session
from .auth import current,admin
from .substitution_schemas import SubstitutionInput
from . import substitution_service
router=APIRouter()
@router.get('/dye-substitutions/overview')
def overview(user=Depends(current),db=Depends(session)):return substitution_service.overview(db)
@router.get('/dye-substitutions/rejections')
def rejections(user=Depends(current),db=Depends(session)):return substitution_service.rejections(db)
@router.post('/dye-substitutions')
def adopt(data:SubstitutionInput,user=Depends(admin),db=Depends(session)):return substitution_service.adopt(db,data,user)
