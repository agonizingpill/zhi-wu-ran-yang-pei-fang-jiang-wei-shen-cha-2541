from datetime import date
from src.fulfillment_models import DyeLot,DyeIssue,ColorDispatch,ShadeComplaint
from src.substitution_models import DyeSubstitution,DyeSubstitutionItem
import math,random
from sqlalchemy import select,func
from src.database import Base,engine,Session
from src.models import User,DyeRecipe,Swatch,Trial
from src.production_models import RecipeRevision,DyeBatch
from src.auth import hash_password

def seed():
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ['admin','colorist','viewer']:
            email=f'{role}@qise.test'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,password=hash_password('Dye2026!'),role=role))
        recipes=[('DY-021','靛蓝亚麻',1.8,.4,.2),('DY-022','苔绿棉麻',.3,1.7,.7),('DY-023','赤陶平纹',.8,.3,1.5)]
        for code,name,a,b,c in recipes:
            if not db.scalar(select(DyeRecipe).where(DyeRecipe.code==code)):db.add(DyeRecipe(code=code,name=name,dye_a=a,dye_b=b,dye_c=c))
        db.flush();rr=db.scalars(select(DyeRecipe).order_by(DyeRecipe.id)).all();rng=random.Random(42)
        for i in range(60):
            lot=['REF-021','SAMPLE-021','SAMPLE-OFF'][i] if i<3 else f'SW-{i:03}'
            if db.scalar(select(Swatch).where(Swatch.lot==lot)):continue
            group=0 if i<3 else i%3
            l,a,b=(48,-2,-25) if i==0 else (48.7,-1.5,-24.5) if i==1 else (62,8,-14) if i==2 else (48+group*9+rng.uniform(-2,2),-2+group*6+rng.uniform(-1,1),-25+group*15+rng.uniform(-1,1))
            spectrum=[round(min(.95,max(.03,.26+group*.11+.1*math.sin(j/5+group)+rng.gauss(0,.012))),5) for j in range(36)]
            db.add(Swatch(recipe_id=rr[group].id,lot=lot,spectrum=spectrum,l=l,a=a,b=b,measured=True))
        prod=db.scalar(select(DyeRecipe).where(DyeRecipe.code=='DY-PROD'))
        if not prod:
            prod=DyeRecipe(code='DY-PROD',name='量产靛蓝基准',dye_a=1.8,dye_b=.4,dye_c=.2,locked=True);db.add(prod);db.flush()
        for lot,l,a,b in [('PROD-REF',48,-2,-25),('PROD-OK',48.5,-1.8,-24.8)]:
            if not db.scalar(select(Swatch).where(Swatch.lot==lot)):db.add(Swatch(recipe_id=prod.id,lot=lot,spectrum=[.3]*36,l=l,a=a,b=b))
        db.flush();ref=db.scalar(select(Swatch).where(Swatch.lot=='PROD-REF'));candidate=db.scalar(select(Swatch).where(Swatch.lot=='PROD-OK'))
        trial=db.scalar(select(Trial).where(Trial.recipe_id==prod.id,Trial.state=='approved'))
        if not trial:
            trial=Trial(recipe_id=prod.id,reference_swatch_id=ref.id,candidate_swatch_id=candidate.id,delta_e=.574456,state='approved',note='量产演示基准');db.add(trial);db.flush()
        if not db.scalar(select(DyeBatch).where(DyeBatch.code=='DB-DEMO-001')):db.add(DyeBatch(code='DB-DEMO-001',approved_trial_id=trial.id,kg=250,target_snapshot={'lot':ref.lot,'recipe_id':prod.id,'l':ref.l,'a':ref.a,'b':ref.b,'trial_delta_e':trial.delta_e}))
        for code,component,kg,expiry,state in [('DYE-A-1117','A',80,date(2027,12,31),'active'),('DYE-B-1117','B',40,date(2027,12,31),'active'),('DYE-C-1117','C',20,date(2027,12,31),'active'),('DYE-C-HOLD','C',5,date(2027,12,31),'quarantine'),('DYE-B-OLD','B',5,date(2026,1,1),'active')]:
            if not db.scalar(select(DyeLot).where(DyeLot.code==code)):db.add(DyeLot(code=code,component=component,available_kg=kg,expires_on=expiry,state=state))
        db.commit()
        print({name:db.scalar(select(func.count()).select_from(model)) for name,model in [('users',User),('recipes',DyeRecipe),('swatches',Swatch)]})
        _seed_substitution(db,prod,trial)

def _seed_substitution(db,prod,trial):
    """换料演示：历史实测学习批次偏色，隔离旧料占用未实测缸，另有证据不足与超色差样本。

    历史缸按“批次相对工艺偏移”生成，使偏色可识别；旧料+新料组合预测色差有区分度。
    """
    lots=[('DYE-A-OLD','A',30,date(2027,6,30),'quarantine'),('DYE-A-NEW','A',60,date(2027,12,31),'active'),
          ('DYE-A-SPARSE','A',50,date(2027,12,31),'active'),('DYE-A-FAR','A',40,date(2027,12,31),'active'),
          ('DYE-A-TINY','A',6.5,date(2027,12,31),'active'),('DYE-B-OLDQ','B',20,date(2027,6,30),'quarantine'),
          ('DYE-C-OLD','C',18,date(2027,6,30),'quarantine'),('DYE-C-NEW','C',15,date(2027,12,31),'active')]
    for code,component,kg,expiry,state in lots:
        if not db.scalar(select(DyeLot).where(DyeLot.code==code)):db.add(DyeLot(code=code,component=component,available_kg=kg,expires_on=expiry,state=state))
    db.flush()
    lot={x.code:x for x in db.scalars(select(DyeLot).where(DyeLot.code.in_([x[0] for x in lots]+['DYE-B-1117','DYE-C-1117']))).all()}
    recipe={'id':prod.id,'code':prod.code,'name':prod.name,'dye_a':prod.dye_a,'dye_b':prod.dye_b,'dye_c':prod.dye_c}
    bias={'DYE-A-OLD':(.95,.40,-.55),'DYE-A-NEW':(-.85,-.30,.45),'DYE-A-SPARSE':(.40,.05,-.10),
          'DYE-A-FAR':(3.6,-2.4,3.2),'DYE-A-TINY':(.18,-.06,.10),
          'DYE-B-1117':(.06,.02,-.05),'DYE-B-OLDQ':(-.10,.15,.20),
          'DYE-C-OLD':(.60,-.30,.70),'DYE-C-NEW':(-.55,.35,-.60),'DYE-C-1117':(.08,.04,.06)}
    target=(48.0,-2.0,-25.0);consumed={}
    def issue(batch_id,code_lot,component,kg):
        consumed[code_lot]=round(consumed.get(code_lot,0.0)+kg,6)
        db.add(DyeIssue(batch_id=batch_id,lot_id=lot[code_lot].id,component=component,
                        kg=kg,recipe_snapshot=recipe,issued_by='colorist@qise.test'))
    history=[('HS-01','DYE-A-OLD','DYE-B-1117','DYE-C-OLD'),('HS-02','DYE-A-NEW','DYE-B-1117','DYE-C-OLD'),
             ('HS-03','DYE-A-OLD','DYE-B-1117','DYE-C-NEW'),('HS-04','DYE-A-NEW','DYE-B-1117','DYE-C-NEW'),
             ('HS-05','DYE-A-OLD','DYE-B-OLDQ','DYE-C-OLD'),('HS-06','DYE-A-NEW','DYE-B-OLDQ','DYE-C-OLD'),
             ('HS-07','DYE-A-OLD','DYE-B-OLDQ','DYE-C-NEW'),('HS-08','DYE-A-NEW','DYE-B-OLDQ','DYE-C-NEW'),
             ('HS-09','DYE-A-SPARSE','DYE-B-1117','DYE-C-1117'),('HS-10','DYE-A-FAR','DYE-B-1117','DYE-C-1117'),
             ('HS-11','DYE-A-FAR','DYE-B-1117','DYE-C-OLD'),('HS-12','DYE-A-FAR','DYE-B-OLDQ','DYE-C-1117'),
             ('HS-13','DYE-A-FAR','DYE-B-OLDQ','DYE-C-OLD'),('HS-14','DYE-A-TINY','DYE-B-1117','DYE-C-1117'),
             ('HS-15','DYE-A-TINY','DYE-B-OLDQ','DYE-C-OLD'),('HS-16','DYE-A-TINY','DYE-B-1117','DYE-C-OLD')]
    for i,(code,a,b,c) in enumerate(history):
        if db.scalar(select(DyeBatch.id).where(DyeBatch.code==code)):continue
        drift=[bias[a][k]+bias[b][k]+bias[c][k]+(0.03 if (i+k)%2 else -0.02) for k in range(3)]
        lab=tuple(round(target[k]+drift[k],3) for k in range(3))
        batch=DyeBatch(code=code,approved_trial_id=trial.id,kg=100,target_snapshot={'lot':'PROD-REF','recipe_id':prod.id,'l':target[0],'a':target[1],'b':target[2],'recipe_snapshot':recipe},
                       lab_l=lab[0],lab_a=lab[1],lab_b=lab[2],delta_e=round(sum(x*x for x in drift)**.5,3),state='qualified')
        db.add(batch);db.flush()
        for component,code_lot in zip('ABC',(a,b,c)):
            issue(batch.id,code_lot,component,round(recipe['dye_'+component.lower()]*batch.kg/100,6))
    scenes=[('DB-HOLD-001',200,('DYE-A-OLD','DYE-B-1117','DYE-C-OLD'),None,'planned'),
            ('DB-HOLD-002',120,('DYE-A-NEW','DYE-B-OLDQ','DYE-C-1117'),None,'planned'),
            ('DB-HOLD-003',150,('DYE-A-NEW','DYE-B-1117','DYE-C-OLD'),None,'planned'),
            ('DB-HOLD-004',100,('DYE-A-OLD','DYE-B-1117','DYE-C-OLD'),None,'planned'),
            ('DB-HOLD-DONE',100,('DYE-A-OLD','DYE-B-1117','DYE-C-OLD'),(49.45,-1.9,-24.85),'hold'),
            ('DB-HOLD-NOEV',90,('DYE-A-SPARSE','DYE-B-1117','DYE-C-OLD'),None,'planned')]
    for code,kg,combo,lab,state in scenes:
        if db.scalar(select(DyeBatch.id).where(DyeBatch.code==code)):continue
        snap={'lot':'PROD-REF','recipe_id':prod.id,'l':target[0],'a':target[1],'b':target[2],'recipe_snapshot':recipe}
        batch=DyeBatch(code=code,approved_trial_id=trial.id,kg=kg,target_snapshot=snap,state=state)
        if lab:
            batch.lab_l,batch.lab_a,batch.lab_b=lab
            batch.delta_e=round(sum((lab[k]-target[k])**2 for k in range(3))**.5,3)
        db.add(batch);db.flush()
        for component,code_lot in zip('ABC',combo):
            issue(batch.id,code_lot,component,round(recipe['dye_'+component.lower()]*batch.kg/100,6))
    # 历史称料与未实测缸占用是真实余额的一部分：只对本次新建的凭据扣减，重复 seed 不会二次扣减。
    for code_lot,kg in consumed.items():
        lot[code_lot].available_kg=round(lot[code_lot].available_kg-kg,6)
    db.commit()

if __name__=='__main__':seed()
