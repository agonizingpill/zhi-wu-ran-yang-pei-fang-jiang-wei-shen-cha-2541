from datetime import date
import numpy as np
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .models import Audit
from .fulfillment_models import DyeLot,DyeIssue
from .production_models import DyeBatch
from .substitution_models import DyeSubstitution,DyeSubstitutionItem
from .recipe_service import log

MIN_EVIDENCE=3          # 每个染料批次至少 3 个已实测缸次，才允许据此学习偏色
RIDGE=0.5               # 小样本共线时的岭回归收缩，避免偏色被噪声放大
INSPECTION_RATE=0.05    # 整缸检验留量：按实际用量另留 5%，随替代料一并扣出
MAX_DELTA_E=3.0         # 推荐预测门限，与签放工艺门限一致；不替代实测
EPS=1e-8


def _learn(db):
    """用历史整单称料与已实测 Lab 学习每个染料批次的加性偏色。

    measured_lab - target_lab = process + beta[lot_A] + beta[lot_B] + beta[lot_C] + error
    process 为不惩罚的整体工艺偏移，batch 偏色为相对偏移，用岭回归分离组分内批次。
    """
    measured=db.scalars(select(DyeBatch).where(DyeBatch.lab_l.is_not(None)).order_by(DyeBatch.id)).all()
    rows=[]
    for batch in measured:
        target=batch.target_snapshot or {}
        if not all(k in target for k in ('l','a','b')):continue
        if batch.lab_a is None or batch.lab_b is None:continue
        issues={x.component:x for x in db.scalars(select(DyeIssue).where(DyeIssue.batch_id==batch.id)).all()}
        if set(issues)!={'A','B','C'}:continue
        rows.append(([issues[c].lot_id for c in 'ABC'],
                     [batch.lab_l-target['l'],batch.lab_a-target['a'],batch.lab_b-target['b']]))
    lots=sorted({lot_id for ids,_ in rows for lot_id in ids})
    index={lot_id:i+1 for i,lot_id in enumerate(lots)}   # 第 0 列是整体工艺偏移
    ctx={'lots':lots,'index':index,'beta':{x:[0.0,0.0,0.0] for x in lots},'process':[0.0,0.0,0.0],
         'counts':{x:0 for x in lots},'samples':len(rows),'error':{},
         'gram_inv':np.zeros((len(lots)+1,len(lots)+1)),'sigma2':np.zeros(3)}
    if not rows or not lots:return ctx
    x=np.zeros((len(rows),len(lots)+1));x[:,0]=1.0
    for r,(ids,_) in enumerate(rows):
        for lot_id in ids:
            x[r,index[lot_id]]+=1.0
            ctx['counts'][lot_id]+=1
    penalty=np.diag([0.0]+[RIDGE]*len(lots))
    gram=x.T@x+penalty
    try:gram_inv=np.linalg.inv(gram)
    except np.linalg.LinAlgError:gram_inv=np.linalg.pinv(gram)
    y=np.array([residual for _,residual in rows],dtype=float)
    coef=gram_inv@x.T@y
    residual=y-x@coef
    leverage=x@gram_inv@x.T
    dof=max(float(len(rows)-np.trace(leverage)),1e-6)
    sigma2=(residual**2).sum(axis=0)/dof
    ctx['process']=[float(v) for v in coef[0]]
    for lot_id,i in index.items():
        ctx['beta'][lot_id]=[float(v) for v in coef[i]]
        ctx['error'][lot_id]=float(np.sqrt(max(gram_inv[i,i]*float(sigma2.sum()),0.0)))
    ctx['gram_inv']=gram_inv;ctx['sigma2']=sigma2
    return ctx


def _predict(ctx,lot_ids,target):
    residual=list(ctx['process'])
    for lot_id in lot_ids:
        if lot_id in ctx['beta']:
            for k in range(3):residual[k]+=ctx['beta'][lot_id][k]
    positions=[0]+[ctx['index'][x] for x in lot_ids if x in ctx['index']]
    variance=float(ctx['gram_inv'][np.ix_(positions,positions)].sum()*float(ctx['sigma2'].sum()))
    return {'lab_l':target['l']+residual[0],'lab_a':target['a']+residual[1],'lab_b':target['b']+residual[2],
            'delta_e':sum(v*v for v in residual)**.5,'inspection_error':variance**.5,'residual':residual}


def _issue_rows(db,batch):
    return {x.component:x for x in db.scalars(select(DyeIssue).where(DyeIssue.batch_id==batch.id)).all()}


def _lot_snapshot(lot):
    if not lot:return None
    return {'id':lot.id,'code':lot.code,'component':lot.component,'available_kg':lot.available_kg,
            'expires_on':lot.expires_on.isoformat(),'state':lot.state}


def _required_kg(batch,snapshot,component):
    required=round(float(snapshot.get('dye_'+component.lower(),0.0))*batch.kg/100.0,6)
    inspect=round(required*INSPECTION_RATE,6)
    return required,inspect,round(required+inspect,6)


def _candidate_row(db,batch,ctx,component,target,issues,old_lot,new_lot,evidence_ok):
    required,inspect,reserved=_required_kg(batch,issues[component].recipe_snapshot or {},component)
    ids=[new_lot.id if c==component else issues[c].lot_id for c in 'ABC']
    prediction=_predict(ctx,ids,target)
    count=ctx['counts'].get(new_lot.id,0)
    row={'lot_id':new_lot.id,'code':new_lot.code,'component':new_lot.component,
         'available_kg':new_lot.available_kg,'expires_on':new_lot.expires_on.isoformat(),
         'state':new_lot.state,'evidence_count':count,'required_kg':required,
         'inspect_kg':inspect,'reserved_kg':reserved,
         'old_lot_available_kg':old_lot.available_kg if old_lot else None,
         'new_lot_available_kg':new_lot.available_kg,
         'predicted_lab_l':prediction['lab_l'],'predicted_lab_a':prediction['lab_a'],
         'predicted_lab_b':prediction['lab_b'],'predicted_delta_e':prediction['delta_e'],
         'inspection_error':prediction['inspection_error'],'reasons':[]}
    if old_lot is not None and new_lot.id==old_lot.id:row['reasons'].append('替代批次不能是旧批次本身')
    if new_lot.state!='active':row['reasons'].append('库存已隔离，不可领用')
    if new_lot.expires_on<date.today():row['reasons'].append('批次已过期，不可领用')
    if count<MIN_EVIDENCE:row['reasons'].append(f'替代批次实测历史仅 {count} 缸，不足 {MIN_EVIDENCE} 缸')
    if not evidence_ok:row['reasons'].append('旧批次实测证据不足，无法比较批次偏色')
    if new_lot.available_kg+EPS<reserved:row['reasons'].append(f'库存余量不足：可用 {new_lot.available_kg:.3f}kg < 需扣 {reserved:.3f}kg')
    if prediction['delta_e']>MAX_DELTA_E:row['reasons'].append(f'预测色差 {prediction["delta_e"]:.3f} 超过门限 {MAX_DELTA_E:.1f}')
    row['recommended']=not row['reasons']
    return row


def _batch_evaluation(db,batch,ctx):
    issues=_issue_rows(db,batch)
    target=batch.target_snapshot or {}
    result={'batch_id':batch.id,'code':batch.code,'kg':batch.kg,'state':batch.state,
            'target':{k:target.get(k) for k in ('lot','l','a','b')},
            'measured':batch.lab_l is not None or batch.lab_a is not None or batch.lab_b is not None,
            'issues':[],'replace_components':[],'components':{},'reasons':[]}
    if len(issues)!=3 or set(issues)!={'A','B','C'}:
        result['reasons'].append('称料凭据不是完整 A/B/C 三组分，无法整缸换料')
        return result
    for component in 'ABC':
        issue=issues[component];lot=db.get(DyeLot,issue.lot_id)
        result['issues'].append({'component':component,'lot_id':issue.lot_id,
                                 'lot_code':lot.code if lot else None,'kg':issue.kg,
                                 'state':lot.state if lot else None,
                                 'expires_on':lot.expires_on.isoformat() if lot else None})
        if lot and lot.state=='quarantine':result['replace_components'].append(component)
    if not result['replace_components']:
        result['reasons'].append('没有仍在隔离状态的旧批次，无需换料')
        return result
    if result['measured']:
        result['reasons'].append('该缸已实测，签放继续以实测色差为准，拒绝换料')
        return result
    if not all(k in target for k in ('l','a','b')):
        result['reasons'].append('缸次缺少目标色快照，不能预测替代色差')
        return result
    missing=[c for c in 'ABC' if ctx['counts'].get(issues[c].lot_id,0)<MIN_EVIDENCE]
    evidence_ok=not missing
    if missing:
        result['reasons'].append('旧批次 '+'/'.join(missing)+f' 组分的已实测缸次不足 {MIN_EVIDENCE} 缸，证据不足，不给建议')
    result['evidence_ok']=evidence_ok
    result['current_prediction']=_predict(ctx,[issues[c].lot_id for c in 'ABC'],target)
    for component in result['replace_components']:
        old_lot=db.get(DyeLot,issues[component].lot_id)
        required,inspect,reserved=_required_kg(batch,issues[component].recipe_snapshot or {},component)
        block={'component':component,'old_lot':_lot_snapshot(old_lot),
               'old_evidence_count':ctx['counts'].get(issues[component].lot_id,0),
               'required_kg':required,'inspect_kg':inspect,'reserved_kg':reserved,
               'candidates':[],'reasons':[]}
        if not old_lot:block['reasons'].append('旧染料批次已不存在，无法退回旧料')
        candidates=[_candidate_row(db,batch,ctx,component,target,issues,old_lot,lot,evidence_ok)
                    for lot in db.scalars(select(DyeLot).where(DyeLot.component==component,
                        DyeLot.id!=issues[component].lot_id).order_by(DyeLot.id)).all()]
        candidates.sort(key=lambda x:(not x['recommended'],x['predicted_delta_e'],-x['available_kg']))
        block['candidates']=candidates[:8]
        if not candidates:block['reasons'].append('该组分没有其他同组分批次可供替换')
        elif not any(x['recommended'] for x in candidates):
            block['reasons'].append('该组分暂无同时满足证据、库存与预测色差的替代批次')
        result['components'][component]=block
    return result


def overview(db):
    ctx=_learn(db)
    lots=[]
    if ctx['lots']:
        records={x.id:x for x in db.scalars(select(DyeLot).where(DyeLot.id.in_(ctx['lots']))).all()}
        for lot_id in ctx['lots']:
            lot=records.get(lot_id)
            if not lot:continue
            lots.append({'lot_id':lot.id,'code':lot.code,'component':lot.component,'state':lot.state,
                         'available_kg':lot.available_kg,'evidence_count':ctx['counts'].get(lot.id,0),
                         'bias':ctx['beta'].get(lot.id,[0,0,0]),'inspection_error':ctx['error'].get(lot.id)})
    batches=db.scalars(select(DyeBatch).where(DyeBatch.state!='released').order_by(DyeBatch.id)).all()
    substituted={x for x in db.scalars(select(DyeSubstitution.batch_id)).all()}
    affected=[];ignored=[]
    for batch in batches:
        # 已实测缸不入推荐；仅扣留缸列出“已实测拒绝换料”，历史合格缸只作学习样本。
        if batch.lab_l is not None:
            if batch.state=='hold':
                ev=_batch_evaluation(db,batch,ctx)
                if ev['replace_components']:
                    ignored.append({'batch_id':batch.id,'code':batch.code,
                                    'measured':True,'reasons':ev['reasons']})
            continue
        ev=_batch_evaluation(db,batch,ctx)
        if not ev['issues']:continue
        if batch.id in substituted:
            if ev['replace_components']:
                ignored.append({'batch_id':batch.id,'code':batch.code,'measured':False,
                                'reasons':['该缸已采纳换料，凭据已保留，不可重复采纳；隔离替代批次如仍在隔离，请待主管解除后领用']})
            continue
        if ev['replace_components']:affected.append(ev)
        elif ev['reasons']:ignored.append({'batch_id':batch.id,'code':batch.code,'measured':False,'reasons':ev['reasons']})
    substitutions=[]
    for sub in db.scalars(select(DyeSubstitution).order_by(DyeSubstitution.id.desc())).all():
        batch=db.get(DyeBatch,sub.batch_id)
        items=db.scalars(select(DyeSubstitutionItem).where(DyeSubstitutionItem.substitution_id==sub.id).order_by(DyeSubstitutionItem.component)).all()
        measured=batch.lab_l is not None and batch.delta_e is not None
        substitutions.append({'id':sub.id,'batch_id':sub.batch_id,'batch_code':batch.code if batch else None,
            'state':sub.state,'adopted_by':sub.adopted_by,'adopted_at':sub.adopted_at,'note':sub.note,
            'total_required_kg':sub.total_required_kg,'total_inspect_kg':sub.total_inspect_kg,
            'total_returned_kg':sub.total_returned_kg,'total_reserved_kg':sub.total_reserved_kg,
            'predicted_lab':[sub.predicted_lab_l,sub.predicted_lab_a,sub.predicted_lab_b],
            'predicted_delta_e':sub.predicted_delta_e,'prediction_error':sub.prediction_error,
            'measured_lab':[batch.lab_l,batch.lab_a,batch.lab_b] if measured else None,
            'measured_delta_e':batch.delta_e if measured else None,
            'inspection_delta_e':abs(batch.delta_e-sub.predicted_delta_e) if measured else None,
            'evidence':sub.evidence,
            'items':[{'component':x.component,'old_lot':x.old_lot_snapshot,'new_lot':x.new_lot_snapshot,
                      'old_kg':x.old_kg,'required_kg':x.required_kg,'inspect_kg':x.inspect_kg,
                      'reserved_kg':x.reserved_kg,'bias':[x.bias_l,x.bias_a,x.bias_b],
                      'predicted_lab':[x.predicted_lab_l,x.predicted_lab_a,x.predicted_lab_b],
                      'predicted_delta_e':x.predicted_delta_e,'inspection_error':x.prediction_error,
                      'evidence':x.evidence} for x in items]})
    return {'thresholds':{'min_evidence':MIN_EVIDENCE,'ridge':RIDGE,
                          'inspection_rate':INSPECTION_RATE,'max_delta_e':MAX_DELTA_E},
            'model':{'samples':ctx['samples'],'lots':lots},
            'affected':affected,'ignored':ignored,'substitutions':substitutions}


def rejections(db):
    rows=db.scalars(select(Audit).where(Audit.action.like('换料拒绝%')).order_by(Audit.id.desc()).limit(20)).all()
    return [{'id':x.id,'actor':x.actor,'reason':x.action,'at':x.at} for x in rows]


def _reject(db,user,batch,reason):
    # 拒绝不产生任何库存或凭据变更；单独提交一条审计，保证页面可追溯拒绝原因。
    log(db,user,f'换料拒绝 {batch.code if batch else ""}：{reason}')
    db.commit()
    raise HTTPException(409,reason)


def _payload(db,sub,idempotent=False):
    items=db.scalars(select(DyeSubstitutionItem).where(DyeSubstitutionItem.substitution_id==sub.id).order_by(DyeSubstitutionItem.component)).all()
    return {'id':sub.id,'batch_id':sub.batch_id,'state':sub.state,'request_id':sub.request_id,
            'adopted_by':sub.adopted_by,'adopted_at':sub.adopted_at,'idempotent':idempotent,
            'total_required_kg':sub.total_required_kg,'total_inspect_kg':sub.total_inspect_kg,
            'total_returned_kg':sub.total_returned_kg,'total_reserved_kg':sub.total_reserved_kg,
            'predicted_delta_e':sub.predicted_delta_e,'prediction_error':sub.prediction_error,
            'items':[{'component':x.component,'old_lot_id':x.old_lot_id,'new_lot_id':x.new_lot_id,
                      'old_kg':x.old_kg,'required_kg':x.required_kg,'inspect_kg':x.inspect_kg,
                      'reserved_kg':x.reserved_kg,'predicted_delta_e':x.predicted_delta_e,
                      'inspection_error':x.prediction_error} for x in items]}


def _lock(db,model,key):
    return db.scalar(select(model).where(model.id==key).with_for_update()
                     .execution_options(populate_existing=True))


def _lock_lots(db,ids):
    return {lot.id:lot for lot in db.scalars(select(DyeLot).where(DyeLot.id.in_(ids))
            .order_by(DyeLot.id).with_for_update()
            .execution_options(populate_existing=True)).all()}


def adopt(db,data,user):
    # 1) 幂等：同一 request_id 无论中断发生在提交前或响应丢失，都只退扣一次。
    existing=db.scalar(select(DyeSubstitution).where(DyeSubstitution.request_id==data.request_id))
    if existing:
        if existing.batch_id!=data.batch_id:raise HTTPException(409,'request_id 已用于其他缸次，请刷新后重新提交')
        return _payload(db,existing,idempotent=True)
    batch=_lock(db,DyeBatch,data.batch_id)
    if not batch:raise HTTPException(404,'生产缸次不存在')
    # 抢到缸锁后重查：并发重试在缸锁释放后能看到已提交的凭据。
    existing=db.scalar(select(DyeSubstitution).where(DyeSubstitution.batch_id==batch.id))
    if existing:
        if existing.request_id==data.request_id:return _payload(db,existing,idempotent=True)
        raise HTTPException(409,'该缸已换料，不可重复采纳；如需追溯请查看换料凭据')
    if batch.state=='released':raise HTTPException(409,'已签放缸次拒绝换料')
    if batch.lab_l is not None or batch.lab_a is not None or batch.lab_b is not None:
        _reject(db,user,batch,'已实测缸次拒绝换料，签放继续以实测色差为准')
    issues=_issue_rows(db,batch)
    if len(issues)!=3 or set(issues)!={'A','B','C'}:
        _reject(db,user,batch,'称料凭据不是完整 A/B/C 三组分，整单不变')
    submitted={x.component:x.new_lot_id for x in data.items}
    if len(submitted)!=len(data.items):raise HTTPException(409,'同一组分不能提交多个替代批次')
    target=batch.target_snapshot or {}
    if not all(k in target for k in ('l','a','b')):
        _reject(db,user,batch,'缸次缺少目标色快照，不能预测替代色差，整单不变')
    # 2) 按批次 ID 排序锁住三个旧批次与所有候选批次，与整单称料同一顺序，跨缸并发不会超领。
    involved=sorted({issues[c].lot_id for c in 'ABC'}|set(submitted.values()))
    locked=_lock_lots(db,involved)
    if len(locked)!=len(involved):raise HTTPException(404,'替代或旧染料批次不存在')
    old_lots={c:locked[issues[c].lot_id] for c in 'ABC'}
    # 3) 采纳时重核隔离：以锁定后的实时状态为准，旧批次已解除隔离即拒绝。
    quarantine=[c for c in 'ABC' if old_lots[c].state=='quarantine']
    if not quarantine:_reject(db,user,batch,'旧批次已全部解除隔离，当前无需换料')
    if set(submitted)!=set(quarantine):
        _reject(db,user,batch,'须为每个仍在隔离的组分分别指定替代批次；缺少任一组分整单不变')
    ctx=_learn(db)
    missing=[c for c in 'ABC' if ctx['counts'].get(old_lots[c].id,0)<MIN_EVIDENCE]
    if missing:
        _reject(db,user,batch,'旧批次 '+'/'.join(missing)+f' 组分实测历史不足 {MIN_EVIDENCE} 缸，证据不足，不给建议')
    plan=[]
    for component in quarantine:
        old_lot=old_lots[component];new_lot=locked[submitted[component]]
        if new_lot.component!=component:
            _reject(db,user,batch,f'{component} 组分替代批次 {new_lot.code} 的组分不匹配，整单不变')
        row=_candidate_row(db,batch,ctx,component,target,issues,old_lot,new_lot,True)
        if not row['recommended']:
            _reject(db,user,batch,f'{component} 组分替代批次 {new_lot.code} 不满足换料条件：'+'；'.join(row['reasons']))
        plan.append((component,old_lot,new_lot,row))
    before=_predict(ctx,[issues[c].lot_id for c in 'ABC'],target)
    # 4) 全部组分在锁内校验通过后才统一退旧扣新；单事务提交，任一组分失败整单不变。
    totals={'required':0.0,'inspect':0.0,'returned':0.0,'reserved':0.0}
    items=[]
    for component,old_lot,new_lot,row in plan:
        issue=issues[component]
        # 先在退扣前快照双方余额，凭据的“用料前/后”才能真实对账。
        old_before=_lot_snapshot(old_lot);new_before=_lot_snapshot(new_lot)
        old_lot.available_kg=round(old_lot.available_kg+issue.kg,6)
        new_lot.available_kg=round(new_lot.available_kg-row['reserved_kg'],6)
        old_after=_lot_snapshot(old_lot);new_after=_lot_snapshot(new_lot)
        item=DyeSubstitutionItem(substitution_id=0,component=component,
            old_lot_id=old_lot.id,new_lot_id=new_lot.id,old_kg=issue.kg,
            required_kg=row['required_kg'],inspect_kg=row['inspect_kg'],reserved_kg=row['reserved_kg'],
            old_lot_snapshot=old_after,new_lot_snapshot=new_after,
            bias_l=ctx['beta'].get(new_lot.id,[0,0,0])[0],bias_a=ctx['beta'].get(new_lot.id,[0,0,0])[1],
            bias_b=ctx['beta'].get(new_lot.id,[0,0,0])[2],
            predicted_lab_l=row['predicted_lab_l'],predicted_lab_a=row['predicted_lab_a'],
            predicted_lab_b=row['predicted_lab_b'],predicted_delta_e=row['predicted_delta_e'],
            prediction_error=row['inspection_error'],
            evidence={'model_samples':ctx['samples'],'min_evidence':MIN_EVIDENCE,
                      'old_evidence_count':ctx['counts'].get(old_lot.id,0),
                      'new_evidence_count':ctx['counts'].get(new_lot.id,0),
                      'inspection_rate':INSPECTION_RATE,'max_delta_e':MAX_DELTA_E,
                      'balance_before':{'old_kg':old_before['available_kg'],'new_kg':new_before['available_kg']},
                      'balance_after':{'old_kg':old_after['available_kg'],'new_kg':new_after['available_kg']}})
        # 称料凭据同步为换料后实际领用：旧料退回原批次，新料含整缸检验留量一并扣出。
        issue.lot_id=new_lot.id;issue.kg=row['reserved_kg']
        totals['required']+=row['required_kg'];totals['inspect']+=row['inspect_kg']
        totals['returned']+=item.old_kg;totals['reserved']+=row['reserved_kg']
        items.append(item)
    final=_predict(ctx,[issues[c].lot_id for c in 'ABC'],target)
    header=DyeSubstitution(batch_id=batch.id,request_id=data.request_id,state='applied',adopted_by=user.email,
        note=data.note,total_required_kg=round(totals['required'],6),total_inspect_kg=round(totals['inspect'],6),
        total_returned_kg=round(totals['returned'],6),total_reserved_kg=round(totals['reserved'],6),
        predicted_lab_l=final['lab_l'],predicted_lab_a=final['lab_a'],predicted_lab_b=final['lab_b'],
        predicted_delta_e=final['delta_e'],prediction_error=final['inspection_error'],
        evidence={'model_samples':ctx['samples'],'min_evidence':MIN_EVIDENCE,'inspection_rate':INSPECTION_RATE,
                  'max_delta_e':MAX_DELTA_E,'before_prediction':before,'replace_components':quarantine})
    try:
        db.add(header);db.flush()
        for item in items:item.substitution_id=header.id;db.add(item)
        log(db,user,f'整缸换料 {batch.code} 退旧{totals["returned"]:.3f}kg 扣新{totals["reserved"]:.3f}kg 检验留量{totals["inspect"]:.3f}kg')
        db.commit()
    except IntegrityError:
        # 并发唯一约束冲突：整个事务回滚，本次退扣不生效，也不会产生第二份凭据。
        db.rollback()
        winner=db.scalar(select(DyeSubstitution).where((DyeSubstitution.request_id==data.request_id)|(DyeSubstitution.batch_id==batch.id)))
        if winner and winner.batch_id==batch.id and winner.request_id==data.request_id:
            return _payload(db,winner,idempotent=True)
        if winner and winner.batch_id==batch.id:
            raise HTTPException(409,'该缸已被另一请求并发采纳换料，本次整单未生效；请刷新查看换料凭据')
        raise HTTPException(409,'request_id 已被另一缸次并发使用，本次整单未生效，请刷新后重新提交')
    db.refresh(header)
    return _payload(db,header)
