import { LandingEgg } from "./EggChart";
import { CYCLE, REGIME_GUIDE, TOP_MODELS } from "./eggGeometry";

type Props = {
  onEnter: () => void;
  onNews?: () => void;
  onFlows?: () => void;
};

export default function Landing({ onEnter, onNews, onFlows }: Props) {
  return (
    <div className="landing">
      <section className="landing-hero">
        <div className="landing-hero-copy fade-up">
          <h1 className="brand landing-brand">Kostolany Watch</h1>
          <p className="landing-headline">시장이 달걀의 어디쯤인지, 확률로 봅니다.</p>
          <p className="landing-sub">
            코스톨라니 6국면을 달걀 외곽 경로 위의 점으로 읽고, 리듬이·눈치왕·파도꾼 세 AI가 각각
            어디를 짚는지 함께 보여 줍니다.
          </p>
          <div className="landing-cta">
            <button type="button" className="btn-primary" onClick={onEnter}>
              서비스 들어가기
            </button>
            {onNews && (
              <button type="button" className="btn-ghost" onClick={onNews}>
                거시 뉴스 데스크
              </button>
            )}
            {onFlows && (
              <button type="button" className="btn-ghost" onClick={onFlows}>
                섹터 흐름
              </button>
            )}
            <a href="#about" className="btn-ghost">
              어떻게 보나요
            </a>
          </div>
        </div>

        <div className="landing-hero-visual fade-up" aria-hidden="true">
          <div className="landing-egg-bleed">
            <LandingEgg />
          </div>
        </div>
      </section>

      <section id="about" className="landing-section">
        <h2>무엇을 하나요</h2>
        <p>
          뉴스와 감정에 휘둘리기 쉬운 “지금 사야 하나, 팔아야 하나”를, 거래량·참여·유동성·심리의
          축으로 나눠 현재 국면 확률을 제시합니다. 꼭지·바닥을 맞히는 예측기가 아니라, 국면을
          읽기 위한 렌즈입니다.
        </p>
      </section>

      <section className="landing-section">
        <h2>여섯 국면</h2>
        <p className="landing-section-lead">
          상승은 달걀 오른쪽을 타고 올라가고, 하락은 왼쪽을 타고 내려옵니다. 표시는 가운데가 아니라
          외곽 경로 위에 있습니다.
        </p>
        <ul className="regime-list">
          {CYCLE.map((code) => {
            const r = REGIME_GUIDE[code];
            return (
              <li key={code}>
                <span className="regime-code" style={{ color: r.color }}>
                  {code}
                </span>
                <span className="regime-name">{r.name}</span>
                <span className="regime-action">{r.action}</span>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="landing-section">
        <h2>세 AI 분석가</h2>
        <p className="landing-section-lead">서로 다른 성격으로 같은 달걀을 봅니다. 합의가 모이면 더 믿어 볼 만합니다.</p>
        <ul className="analyst-list">
          {TOP_MODELS.map((m) => (
            <li key={m.id}>
              <span className="analyst-dot" style={{ background: m.color }} />
              <div>
                <strong style={{ color: m.color }}>{m.label}</strong>
                <em>{m.trait}</em>
                <p>{m.blurb}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="landing-section">
        <h2>정직한 기대치</h2>
        <p>
          완결된 시장 사이클은 드물고, 과거 패턴이 그대로 반복된다는 보장은 없습니다. 그래서 결과는
          항상 확률과 불확실성으로 보여 주고, 과거 달걀 리플레이로 판단의 궤적을 투명하게 남깁니다.
        </p>
      </section>

      <section className="landing-section landing-finale">
        <h2>달걀 위에서 시작해 보세요</h2>
        <p>한 번 누르면 바로 국면 화면으로 들어갑니다.</p>
        <button type="button" className="btn-primary" onClick={onEnter}>
          서비스 들어가기
        </button>
        <p className="disclaimer landing-disclaimer">
          본 정보는 교육·연구 목적의 국면 인식 보조 자료이며 투자 권유·자문이 아닙니다. 투자 판단과
          손실에 대한 책임은 이용자 본인에게 있습니다.
        </p>
      </section>
    </div>
  );
}
