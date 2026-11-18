from datetime import datetime
from sqlalchemy import String,Float,ForeignKey,JSON,DateTime,CheckConstraint,UniqueConstraint,func
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base

class DyeSubstitution(Base):
    """主管采纳的一次整缸换料凭据（单头），request_id 供中断重试幂等使用。"""
    __tablename__='dye_substitutions'
    __table_args__=(CheckConstraint('predicted_delta_e>=0'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    batch_id:Mapped[int]=mapped_column(ForeignKey('dye_batches.id'),unique=True)
    request_id:Mapped[str]=mapped_column(String(80),unique=True)
    state:Mapped[str]=mapped_column(String(24),default='applied')
    adopted_by:Mapped[str]=mapped_column(String(120),default='')
    adopted_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
    note:Mapped[str]=mapped_column(String(240),default='')
    total_required_kg:Mapped[float]=mapped_column(Float)
    total_inspect_kg:Mapped[float]=mapped_column(Float)
    total_returned_kg:Mapped[float]=mapped_column(Float)
    total_reserved_kg:Mapped[float]=mapped_column(Float)
    predicted_lab_l:Mapped[float]=mapped_column(Float)
    predicted_lab_a:Mapped[float]=mapped_column(Float)
    predicted_lab_b:Mapped[float]=mapped_column(Float)
    predicted_delta_e:Mapped[float]=mapped_column(Float)
    prediction_error:Mapped[float]=mapped_column(Float,default=0.0)
    evidence:Mapped[dict]=mapped_column(JSON)

class DyeSubstitutionItem(Base):
    """换料凭据的组分分明细：保留用料前后、学习偏色、检验留量与预测误差。"""
    __tablename__='dye_substitution_items'
    __table_args__=(UniqueConstraint('substitution_id','component'),CheckConstraint("component IN ('A','B','C') AND required_kg>=0 AND inspect_kg>=0 AND reserved_kg>=0 AND old_kg>=0"))
    id:Mapped[int]=mapped_column(primary_key=True)
    substitution_id:Mapped[int]=mapped_column(ForeignKey('dye_substitutions.id'))
    component:Mapped[str]=mapped_column(String(1))
    old_lot_id:Mapped[int]=mapped_column(ForeignKey('dye_lots.id'))
    new_lot_id:Mapped[int]=mapped_column(ForeignKey('dye_lots.id'))
    old_kg:Mapped[float]=mapped_column(Float)
    required_kg:Mapped[float]=mapped_column(Float)
    inspect_kg:Mapped[float]=mapped_column(Float)
    reserved_kg:Mapped[float]=mapped_column(Float)
    old_lot_snapshot:Mapped[dict]=mapped_column(JSON)
    new_lot_snapshot:Mapped[dict]=mapped_column(JSON)
    bias_l:Mapped[float]=mapped_column(Float)
    bias_a:Mapped[float]=mapped_column(Float)
    bias_b:Mapped[float]=mapped_column(Float)
    predicted_lab_l:Mapped[float]=mapped_column(Float)
    predicted_lab_a:Mapped[float]=mapped_column(Float)
    predicted_lab_b:Mapped[float]=mapped_column(Float)
    predicted_delta_e:Mapped[float]=mapped_column(Float)
    prediction_error:Mapped[float]=mapped_column(Float,default=0.0)
    evidence:Mapped[dict]=mapped_column(JSON)
