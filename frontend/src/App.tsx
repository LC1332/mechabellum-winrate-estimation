import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  DndContext,
  DragEndEvent,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";

type Side = "a" | "b";
type Lane = -2 | -1 | 0 | 1 | 2;
type CatalogUnit = {
  unit_id: number; name_cn: string; name_en: string; axis: number;
  unlock_cost: number; base_buy_cost: number; upgrade_cost_per_level: number | null; unlock_tier: number; icon_path: string;
};
type Formation = { id: string; unit_id: number; lane: Lane; level: number };
type SideState = { balance: number; unlocked: number[]; tech: Record<number, number>; formations: Formation[] };
type Recommendation = { unit_id: number; name_cn: string; score: number; score_percent: number; unlocked: boolean; icon_path: string };
type Evaluation = {
  probability: { side_a: number; side_b: number; fold_std: number };
  recommendations: { side_a: Record<string, Recommendation[]>; side_b: Record<string, Recommendation[]> };
  model: { name: string; folds: number; feature_dim: number; temperature: number };
};

const LANES: Lane[] = [-2, -1, 0, 1, 2];
const LANE_LABEL: Record<Lane, string> = { [-2]: "左", [-1]: "左中", [0]: "中", [1]: "右中", [2]: "右" };
const PROBE_LABELS = ["左路", "中路", "右路"];
const MAX_LEVEL = 9;
const EMPTY_SIDE = (units: CatalogUnit[] = []): SideState => ({ balance: 900, unlocked: units.filter((unit) => unit.unlock_cost === 0).map((unit) => unit.unit_id), tech: {}, formations: [] });

function iconFor(unit: { name_cn: string; icon_path?: string }, className = "unit-icon") {
  return <img className={className} src={unit.icon_path || ""} alt={unit.name_cn} onError={(event) => {
    const node = event.currentTarget;
    node.style.display = "none";
    node.parentElement?.classList.add("icon-fallback");
    if (node.parentElement && !node.parentElement.dataset.label) node.parentElement.dataset.label = unit.name_cn.slice(0, 1);
  }} />;
}

function money(value: number) { return `${Math.round(value).toLocaleString("zh-CN")} 金`; }

function formationValue(unit: CatalogUnit, formation: Formation) {
  return unit.base_buy_cost + Math.max(0, formation.level - 1) * (unit.upgrade_cost_per_level || 0);
}

function DraggableFormation({ formation, unit, side, balance, onUpgrade, onRemove }: { formation: Formation; unit: CatalogUnit; side: Side; balance: number; onUpgrade: () => void; onRemove?: () => void }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: formation.id, data: { kind: "formation", formation } });
  const upgradeCost = unit.upgrade_cost_per_level;
  const canUpgrade = formation.level < MAX_LEVEL && upgradeCost !== null && balance >= upgradeCost;
  return <div ref={setNodeRef} {...listeners} {...attributes} className={`formation ${side === "a" ? "formation-a" : "formation-b"} ${isDragging ? "dragging" : ""}`} style={{ transform: CSS.Translate.toString(transform) }} title="拖动调整分路">
    <div className="formation-main"><span className="icon-box">{iconFor(unit)}</span><span className="formation-name">{unit.name_cn}<small>Lv.{formation.level} · {money(formationValue(unit, formation))}</small></span><button className="upgrade-button" disabled={!canUpgrade} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); onUpgrade(); }} aria-label={`升级${unit.name_cn}`}>{formation.level >= MAX_LEVEL ? "Lv.9" : `升级 ¥${upgradeCost || 0}`}</button>{onRemove && <button className="mini-button" onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); onRemove(); }} aria-label={`出售${unit.name_cn}`}>×</button>}</div>
  </div>;
}

function ShopCard({ unit, side, unlocked, onBuy }: { unit: CatalogUnit; side: Side; unlocked: boolean; onBuy: () => void }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: `shop:${side}:${unit.unit_id}`,
    disabled: !unlocked,
    data: { kind: "shop", side, unit },
  });
  return <button ref={setNodeRef} {...listeners} {...attributes} className={`shop-card ${unlocked ? "unlocked" : "locked"} ${isDragging ? "dragging" : ""}`} style={{ transform: CSS.Translate.toString(transform) }} onClick={onBuy} disabled={false} aria-label={`${unit.name_cn}${unlocked ? "，拖动到战场购买" : "，点击解锁"}`}>
    <span className="icon-box">{iconFor(unit)}</span><strong>{unit.name_cn}</strong><small>{unlocked ? `购买 ¥${unit.base_buy_cost}` : `解锁 ¥${unit.unlock_cost}`}</small>{!unlocked && <span className="lock-label">未解锁</span>}
  </button>;
}

function LaneDropZone({ side, lane, active, onSelect, children }: { side: Side; lane: Lane; active: boolean; onSelect: () => void; children: ReactNode }) {
  const { isOver, setNodeRef } = useDroppable({ id: `lane:${side}:${lane}`, data: { side, lane } });
  return <div ref={setNodeRef} className={`lane-zone ${side === "a" ? "lane-a" : "lane-b"} ${active ? "active" : ""} ${isOver ? "over" : ""}`} onClick={onSelect}>
    <div className="lane-head"><span>{LANE_LABEL[lane]}</span><span>{active ? "已选" : "点击部署"}</span></div>{children}
  </div>;
}

function TrashZone({ side }: { side: Side }) {
  const { isOver, setNodeRef } = useDroppable({ id: `trash:${side}`, data: { side } });
  return <div ref={setNodeRef} className={`trash-zone ${side === "a" ? "trash-a" : "trash-b"} ${isOver ? "over" : ""}`}>🗑 拖到这里出售</div>;
}

function App() {
  const [catalog, setCatalog] = useState<CatalogUnit[]>([]);
  const [sideA, setSideA] = useState<SideState>(EMPTY_SIDE);
  const [sideB, setSideB] = useState<SideState>(EMPTY_SIDE);
  const [round, setRound] = useState(1);
  const [target, setTarget] = useState<{ side: Side; lane: Lane }>({ side: "a", lane: 0 });
  const [temperature, setTemperature] = useState(5);
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [health, setHealth] = useState(true);
  const idCounter = useRef(1);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));

  useEffect(() => {
    Promise.all([fetch("/api/catalog").then((response) => response.json()), fetch("/api/health")]).then(([catalogPayload, healthResponse]) => {
      const units = catalogPayload.units || [];
      setCatalog(units);
      setSideA((state) => ({ ...state, unlocked: Array.from(new Set([...state.unlocked, ...units.filter((unit: CatalogUnit) => unit.unlock_cost === 0).map((unit: CatalogUnit) => unit.unit_id)])) }));
      setSideB((state) => ({ ...state, unlocked: Array.from(new Set([...state.unlocked, ...units.filter((unit: CatalogUnit) => unit.unlock_cost === 0).map((unit: CatalogUnit) => unit.unit_id)])) }));
      setHealth(healthResponse.ok);
    }).catch(() => { setHealth(false); setError("后端尚未启动，请先运行 uvicorn backend.run:app --reload"); });
  }, []);

  const unitById = useMemo(() => new Map(catalog.map((unit) => [unit.unit_id, unit])), [catalog]);
  const tiers = useMemo(() => [0, 50, 200, 350].map((tier) => ({ tier, units: catalog.filter((unit) => unit.unlock_tier === tier) })), [catalog]);
  const trackedUnits = useMemo(() => {
    const ids = new Set([...Object.keys(sideA.tech).map(Number), ...Object.keys(sideB.tech).map(Number), ...sideA.formations.map((item) => item.unit_id), ...sideB.formations.map((item) => item.unit_id)]);
    return catalog.filter((unit) => ids.has(unit.unit_id));
  }, [catalog, sideA, sideB]);

  function mutateSide(side: Side, mutation: (current: SideState) => SideState) {
    side === "a" ? setSideA((current) => mutation(current)) : setSideB((current) => mutation(current));
    setEvaluation(null);
  }

  function selectTarget(side: Side, lane: Lane) { setTarget({ side, lane }); setError(""); }

  function deployUnit(side: Side, lane: Lane, unit: CatalogUnit) {
    const current = side === "a" ? sideA : sideB;
    if (!current.unlocked.includes(unit.unit_id)) { setError(`${unit.name_cn} 尚未解锁，请先点击商店卡片解锁。`); return; }
    if (current.balance < unit.base_buy_cost) { setError(`${unit.name_cn} 购买需要 ${money(unit.base_buy_cost)}，当前不足。`); return; }
    const formation: Formation = { id: `formation-${idCounter.current++}`, unit_id: unit.unit_id, lane, level: 1 };
    mutateSide(side, (state) => ({ ...state, balance: state.balance - unit.base_buy_cost, formations: [...state.formations, formation] }));
    setError("");
  }

  function buyOrUnlock(unit: CatalogUnit) {
    const current = target.side === "a" ? sideA : sideB;
    if (!current.unlocked.includes(unit.unit_id)) {
      if (current.balance < unit.unlock_cost) { setError(`${unit.name_cn} 解锁需要 ${money(unit.unlock_cost)}，当前不足。`); return; }
      mutateSide(target.side, (state) => ({ ...state, balance: state.balance - unit.unlock_cost, unlocked: [...state.unlocked, unit.unit_id] }));
      setError(`${unit.name_cn} 已解锁，请再次点击购买或拖动卡片部署。`); return;
    }
    deployUnit(target.side, target.lane, unit);
  }

  function sell(side: Side, id: string) {
    const current = side === "a" ? sideA : sideB;
    const formation = current.formations.find((item) => item.id === id); if (!formation) return;
    const unit = unitById.get(formation.unit_id); if (!unit) return;
    mutateSide(side, (state) => ({ ...state, balance: state.balance + formationValue(unit, formation), formations: state.formations.filter((item) => item.id !== id) }));
  }

  function upgrade(side: Side, id: string) {
    const current = side === "a" ? sideA : sideB;
    const formation = current.formations.find((item) => item.id === id); if (!formation) return;
    const unit = unitById.get(formation.unit_id); if (!unit) return;
    const cost = unit.upgrade_cost_per_level;
    if (formation.level >= MAX_LEVEL) { setError(`${unit.name_cn} 已达到最高等级 Lv.${MAX_LEVEL}。`); return; }
    if (cost === null) { setError(`${unit.name_cn} 暂无可用的升级费用。`); return; }
    if (current.balance < cost) { setError(`${unit.name_cn} 升级需要 ${money(cost)}，当前不足。`); return; }
    mutateSide(side, (state) => ({
      ...state,
      balance: state.balance - cost,
      formations: state.formations.map((item) => item.id === id ? { ...item, level: item.level + 1 } : item),
    }));
    setError("");
  }

  function dragEnd(event: DragEndEvent) {
    const payload = event.active.data.current as { kind?: string; formation?: Formation; side?: Side; unit?: CatalogUnit } | undefined;
    const destination = event.over?.data.current as { side?: Side; lane?: Lane } | undefined;
    if (!event.over || !payload) return;
    if (payload.kind === "shop") {
      if (!payload.unit || !payload.side || !destination?.side || destination.lane === undefined || destination.side !== payload.side) return;
      deployUnit(payload.side, destination.lane, payload.unit);
      return;
    }
    const formation = payload.formation;
    if (!formation) return;
    if (event.over.id.toString().startsWith("trash:")) {
      const trashSide = event.over.id.toString().split(":")[1] as Side;
      const owner = sideA.formations.some((item) => item.id === formation.id) ? "a" : "b";
      if (trashSide === owner) sell(trashSide, formation.id);
      return;
    }
    if (!destination?.side || destination.lane === undefined) return;
    const owner = sideA.formations.some((item) => item.id === formation.id) ? "a" : "b";
    if (owner !== destination.side) return;
    mutateSide(owner, (state) => ({ ...state, formations: state.formations.map((item) => item.id === formation.id ? { ...item, lane: destination.lane! } : item) }));
  }

  function changeTech(side: Side, unit: CatalogUnit, delta: number) {
    const current = side === "a" ? sideA : sideB;
    const old = current.tech[unit.unit_id] || 0; const next = old + delta;
    if (next < 0 || current.balance < delta) return;
    mutateSide(side, (state) => ({ ...state, balance: state.balance - delta, tech: { ...state.tech, [unit.unit_id]: next } }));
  }

  function addMoney(side: Side, amount: number) { if (!Number.isFinite(amount) || amount <= 0) return; mutateSide(side, (state) => ({ ...state, balance: state.balance + Math.floor(amount) })); }

  async function calculate() {
    if (!sideA.formations.length || !sideB.formations.length) { setError("双方都至少需要部署一个单位后才能计算。"); return; }
    setLoading(true); setError("");
    try {
      const response = await fetch("/api/evaluate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ temperature, side_a: { formations: sideA.formations, unlocked_unit_ids: sideA.unlocked, tech_investment: sideA.tech }, side_b: { formations: sideB.formations, unlocked_unit_ids: sideB.unlocked, tech_investment: sideB.tech } }) });
      const payload = await response.json(); if (!response.ok) throw new Error(payload.detail?.message || "计算失败"); setEvaluation(payload);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "计算失败"); } finally { setLoading(false); }
  }

  function nextRound() { const next = round + 1; setRound(next); setSideA((state) => ({ ...state, balance: state.balance + next * 200 })); setSideB((state) => ({ ...state, balance: state.balance + next * 200 })); setEvaluation(null); }
  function reset() { setSideA(EMPTY_SIDE(catalog)); setSideB(EMPTY_SIDE(catalog)); setRound(1); setTarget({ side: "a", lane: 0 }); setTemperature(5); setEvaluation(null); setError(""); }

  return <div className="app-shell">
    <header className="topbar"><div><p className="eyebrow">MECHABELLUM · FIRST FRONT</p><h1>胜率模拟器</h1></div><div className="top-actions"><span className="round-pill">第 {round} 回合</span><button className="secondary" onClick={reset}>重置</button></div></header>
    {error && <div className="notice error">{error}</div>}
    {!health && <div className="notice warning">后端模型不可用。请在仓库根目录启动 <code>uvicorn backend.run:app --reload</code>。</div>}
    <DndContext sensors={sensors} onDragEnd={dragEnd}>
      <main className="workspace">
        <aside className="panel tech-panel"><div className="panel-title"><span>科技与投资</span><small>只记录通用资金投入</small></div>
          {(["b", "a"] as Side[]).map((side) => { const state = side === "a" ? sideA : sideB; return <section className={`invest-side ${side}`} key={side}><div className="side-heading"><span className="side-dot" />{side === "a" ? "我方 A" : "对方 B"}<strong>{money(state.balance)}</strong></div><div className="money-tools"><button onClick={() => addMoney(side, 100)}>+100</button><button onClick={() => { const amount = Number(window.prompt("输入要增加的整数金额", "100")); addMoney(side, amount); }}>自定义</button></div>{trackedUnits.length === 0 ? <p className="muted">部署单位后可调整科技投资。</p> : <div className="investment-list">{trackedUnits.map((unit) => <div className="investment-row" key={`${side}-${unit.unit_id}`}><span className="icon-box small">{iconFor(unit)}</span><span>{unit.name_cn}</span><span className="tech-value">{money(state.tech[unit.unit_id] || 0)}</span><button className="tiny" onClick={() => changeTech(side, unit, -100)} disabled={!state.tech[unit.unit_id]}>−</button><button className="tiny" onClick={() => changeTech(side, unit, 100)} disabled={state.balance < 100}>+</button></div>)}</div>}</section>; })}
        </aside>
        <section className="center-column"><div className="battle-header"><div><span className="eyebrow">BATTLEFIELD</span><h2>五栏分路部署</h2><p className="temperature-control"><label htmlFor="temperature">温度 {temperature}</label><input id="temperature" type="range" min="1" max="20" step="1" value={temperature} onChange={(event) => { setTemperature(Number(event.target.value)); setEvaluation(null); }} /><small>越高越平滑</small></p></div><div className="battle-actions"><button className="supply-button" onClick={nextRound}>进入第 {round + 1} 回合 · +{(round + 1) * 200} 金</button><button className="calculate" onClick={calculate} disabled={loading || !health || !sideA.formations.length || !sideB.formations.length}>{loading ? "计算中…" : "计算胜率与推荐"}</button></div></div><p className="helper">点击栏位选择购买目标；已解锁单位也可以从右侧拖入对应阵营的任意分路。拖动场上单位可以调整分路，拖入本方垃圾桶出售。</p>
          <div className="battlefield"><div className="battle-side-label side-b-label"><span>B · 对方</span><TrashZone side="b" /></div><div className="lanes lanes-b">{LANES.map((lane) => <LaneDropZone key={`b-${lane}`} side="b" lane={lane} active={target.side === "b" && target.lane === lane} onSelect={() => selectTarget("b", lane)}>{sideB.formations.filter((item) => item.lane === lane).map((item) => { const unit = unitById.get(item.unit_id); return unit ? <DraggableFormation key={item.id} formation={item} unit={unit} side="b" balance={sideB.balance} onUpgrade={() => upgrade("b", item.id)} onRemove={() => sell("b", item.id)} /> : null; })}</LaneDropZone>)}</div><div className="midline"><span>y = −200</span><i>◈</i><span>y = 200</span></div><div className="lanes lanes-a">{LANES.map((lane) => <LaneDropZone key={`a-${lane}`} side="a" lane={lane} active={target.side === "a" && target.lane === lane} onSelect={() => selectTarget("a", lane)}>{sideA.formations.filter((item) => item.lane === lane).map((item) => { const unit = unitById.get(item.unit_id); return unit ? <DraggableFormation key={item.id} formation={item} unit={unit} side="a" balance={sideA.balance} onUpgrade={() => upgrade("a", item.id)} onRemove={() => sell("a", item.id)} /> : null; })}</LaneDropZone>)}</div><div className="battle-side-label side-a-label"><span>A · 我方</span><TrashZone side="a" /></div></div>
          {evaluation && <section className="results"><div className="result-score"><div><span>我方胜率</span><strong>{(evaluation.probability.side_a * 100).toFixed(1)}%</strong></div><div className="meter"><span style={{ width: `${evaluation.probability.side_a * 100}%` }} /></div><small>温度 {evaluation.model.temperature} · 模型波动 ±{(evaluation.probability.fold_std * 100).toFixed(2)} 个百分点 · AUC≈0.607</small></div><div className="recommendation-grid">{(["side_a", "side_b"] as const).map((side) => <div className="recommendation-side" key={side}><h3>{side === "side_a" ? "我方推荐" : "对方推荐"}</h3><div className="recommendation-columns">{PROBE_LABELS.map((probe, index) => { const laneKey = index === 0 ? "left" : index === 1 ? "middle" : "right"; const list = evaluation.recommendations[side][laneKey] || []; return <div key={probe}><h4>{probe}</h4>{list.map((item) => <div className="recommendation" key={item.unit_id}><span className="icon-box small">{iconFor(item)}</span><span><strong>{item.name_cn}</strong><small>{item.unlocked ? "已解锁" : "需解锁"}</small></span><em>{item.score_percent >= 0 ? "+" : ""}{item.score_percent.toFixed(2)}%</em></div>)}</div>; })}</div></div>)}</div></section>}
        </section>
        <aside className="panel shop-panel"><div className="panel-title"><span>购买单位</span><small>拖动已解锁卡片到战场即可购买</small><div className="side-switch" role="group" aria-label="购买阵营"><button className={target.side === "b" ? "selected side-b" : "side-b"} onClick={() => setTarget((current) => ({ ...current, side: "b" }))}>对方</button><button className={target.side === "a" ? "selected side-a" : "side-a"} onClick={() => setTarget((current) => ({ ...current, side: "a" }))}>我方</button></div><span className="shop-target">目标：{target.side === "a" ? "我方" : "对方"} · {LANE_LABEL[target.lane]}</span></div>{tiers.map(({ tier, units }) => <section className="shop-tier" key={tier}><h3><span>{tier === 0 ? "基础单位" : `解锁费 ${tier}`}</span><small>{units.length} 个单位</small></h3><div className="shop-grid">{units.map((unit) => { const unlocked = (target.side === "a" ? sideA : sideB).unlocked.includes(unit.unit_id); return <ShopCard key={unit.unit_id} unit={unit} side={target.side} unlocked={unlocked} onBuy={() => buyOrUnlock(unit)} />; })}</div></section>)}</aside>
      </main>
    </DndContext>
    <footer>full-6-7 Logistic ensemble · 3 folds · 6235 features · 结果仅作策略参考</footer>
  </div>;
}

export default App;
