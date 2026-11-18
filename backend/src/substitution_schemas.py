from typing import Literal
from pydantic import BaseModel,Field,ConfigDict,field_validator
class SubstitutionInput(BaseModel):
    """主管采纳整缸换料，必须为本次请求携带客户端生成的幂等 request_id。"""
    model_config=ConfigDict(allow_inf_nan=False)
    batch_id:int=Field(gt=0)
    request_id:str=Field(min_length=8,max_length=80)
    note:str=Field(default='',max_length=240)
    items:list['SubstitutionItemInput']=Field(min_length=1,max_length=3)
    @field_validator('request_id','note')
    @classmethod
    def trim(cls,v):return v.strip() if isinstance(v,str) else v
    @field_validator('items')
    @classmethod
    def distinct(cls,v):
        if len({x.component for x in v})!=len(v):raise ValueError('同一组分只能指定一个替代批次')
        return v
class SubstitutionItemInput(BaseModel):
    model_config=ConfigDict(allow_inf_nan=False)
    component:Literal['A','B','C']
    new_lot_id:int=Field(gt=0)
