import type { Metadata } from "next";
import Link from "next/link";
import Disclaimer from "@/components/Disclaimer";
import StatusBadge from "@/components/StatusBadge";
import type { EventStatus } from "@/lib/types";

export const metadata: Metadata = { title: "소개" };

/** 사건 하나가 지나는 다섯 단계 — 타임라인 화면의 단계와 같다 */
const LIFECYCLE: Array<{ name: string; text: string }> = [
  { name: "발생", text: "사고·재난이 일어나고 처음 보도됩니다." },
  { name: "원인 조사", text: "왜 일어났는지, 누구 책임인지 조사 소식이 이어집니다." },
  { name: "대응 발표", text: "기관이 \"언제까지 무엇을 하겠다\"고 밝힙니다. 미결이 기록하는 약속입니다." },
  { name: "조치 진행", text: "공사 착수, 예산 확보처럼 약속이 움직이는 소식입니다." },
  { name: "결과 확인", text: "완료·개통·준공처럼 약속이 마무리됐다는 소식입니다." },
];

const STATUS_TEXT: Array<{ status: EventStatus; text: string }> = [
  { status: "조치 완료 근거 확인", text: "발표된 조치가 끝났다고 전한 후속 기사가 있습니다." },
  { status: "조치 진행 정황 확인", text: "착공·추진처럼 조치가 진행 중이라는 후속 기사가 있습니다. \"완료 예정\"도 여기에 듭니다." },
  { status: "새로운 문제 발생", text: "같은 시설·사건에서 문제가 다시 생겼거나 조치가 늦어졌다는 보도가 있습니다." },
  { status: "후속보도 부족", text: "후속 기사가 없거나 오래 끊겼습니다. 발표 기한이 지났는데 근거가 없는 경우도 여기에 듭니다." },
  { status: "판단 불가", text: "후속 기사는 있지만 조치를 판단할 근거가 없거나 서로 엇갈립니다." },
];

const NEXT_STEPS: Array<{ title: string; text: string }> = [
  { title: "정치인 공약", text: "선거 때 밝힌 공약이 임기 중 어떻게 보도됐는지 같은 방식으로 따라갑니다." },
  { title: "기업의 공개 약속", text: "사고 뒤 기업이 공개적으로 밝힌 재발 방지 약속을 추적합니다." },
  { title: "기관별 모아보기", text: "한 기관이 발표한 약속과 후속보도를 한 화면에 모읍니다." },
  { title: "후속보도 순위", text: "기관별 후속보도 현황을 기간별로 비교해 보여 줍니다." },
];

export default function AboutPage() {
  return (
    <div className="flex flex-col gap-10">
      <section className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-slate-950 via-slate-900 to-indigo-950 px-5 py-9 text-white sm:px-8">
        <div aria-hidden className="pointer-events-none absolute -right-20 -top-24 h-72 w-72 rounded-full bg-indigo-500/20 blur-3xl" />
        <p className="relative text-xs font-semibold tracking-wide text-sky-300">미결(未結) · 끝나지 않은 뉴스를 추적합니다</p>
        <h1 className="relative mt-2 text-2xl font-bold leading-snug sm:text-3xl">
          뉴스는 끝났지만,
          <br />
          문제도 끝났습니까?
        </h1>
        <p className="relative mt-4 max-w-2xl text-sm leading-relaxed text-slate-300 sm:text-base">
          큰 사고가 나면 기관은 대책을 발표하고, 뉴스는 곧 다른 소식으로 넘어갑니다. 미결은 재난·안전 사건에서 공공기관이 밝힌
          약속(누가, 무엇을, 언제까지)을 기록하고, 그 뒤에 나온 기사로 약속이 어떻게 됐는지를 다섯 가지 상태로 보여 줍니다.
          후속보도가 끊긴 사건은 목록 위쪽에 올려 다시 들여다볼 수 있게 합니다.
        </p>
        <div className="relative mt-5 flex flex-wrap gap-2 text-sm">
          <Link href="/" className="rounded-lg bg-white px-3.5 py-2 font-semibold text-slate-900 hover:bg-slate-100">
            대시보드 보기
          </Link>
          <Link href="/events" className="rounded-lg px-3.5 py-2 font-semibold text-white ring-1 ring-white/30 hover:bg-white/10">
            확인이 필요한 사건
          </Link>
        </div>
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-bold text-gray-900">사건 하나가 지나는 다섯 단계</h2>
        <ol className="grid grid-cols-1 gap-3 sm:grid-cols-5">
          {LIFECYCLE.map((step, i) => (
            <li key={step.name} className="relative rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
              <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gray-900 text-xs font-bold text-white">{i + 1}</span>
              <p className="mt-2 font-semibold text-gray-900">{step.name}</p>
              <p className="mt-1 text-sm leading-relaxed text-gray-600">{step.text}</p>
            </li>
          ))}
        </ol>
        <p className="text-sm text-gray-600">
          사건마다 기사들을 이 다섯 단계로 나눠 타임라인으로 보여 줍니다. 대응 발표 뒤에 조치 진행·결과 확인 기사가 이어지는지가
          핵심입니다.
        </p>
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-bold text-gray-900">다섯 가지 상태</h2>
        <ul className="flex flex-col divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white">
          {STATUS_TEXT.map((s) => (
            <li key={s.status} className="flex flex-col gap-2 p-4 sm:flex-row sm:items-center sm:gap-4">
              <span className="sm:w-48 sm:shrink-0">
                <StatusBadge status={s.status} />
              </span>
              <span className="text-sm leading-relaxed text-gray-700">{s.text}</span>
            </li>
          ))}
        </ul>
        <Disclaimer />
        <p className="text-sm text-gray-600">
          상태는 기사에서 찾은 근거로만 정합니다. 사건 화면의 &lsquo;근거 보기&rsquo;에서 어떤 기사의 어떤 문장을 근거로 삼았는지 확인할 수
          있고, 잘못된 연결은 신고할 수 있습니다.
        </p>
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-bold text-gray-900">데이터는 어디서 오나요</h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="rounded-xl border border-gray-200 bg-white p-4">
            <p className="font-semibold text-gray-900">빅카인즈 뉴스·인용문</p>
            <p className="mt-1 text-sm leading-relaxed text-gray-600">
              한국언론진흥재단 빅카인즈 API로 기사 제목, 200자 요약, 기사 속 인용문을 받습니다. 기사 원문은 저장하지 않고, 제목을 누르면
              언론사 페이지로 연결됩니다.
            </p>
          </div>
          <div className="rounded-xl border border-gray-200 bg-white p-4">
            <p className="font-semibold text-gray-900">기관 공식 게시판</p>
            <p className="mt-1 text-sm leading-relaxed text-gray-600">
              후속보도가 오래 없는 사건은 책임 기관 홈페이지의 보도자료·공지사항 목록을 확인해, 기관이 따로 발표한 내용이 있으면 보조
              근거로 함께 보여 줍니다.
            </p>
          </div>
        </div>
        <p className="text-sm text-gray-600">
          대상은 공공기관·지자체·공공시설의 공개된 공식 발언뿐입니다. 개인은 추적하지 않고, 발언한 사람은 소속 기관 단위로 묶습니다.
        </p>
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-bold text-gray-900">다음 단계</h2>
        <p className="text-sm text-gray-600">같은 방식으로 추적 대상을 넓혀 갈 계획입니다. 아직 서비스에는 없습니다.</p>
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {NEXT_STEPS.map((n) => (
            <li key={n.title} className="rounded-xl border border-dashed border-gray-300 bg-white/60 p-4">
              <p className="flex items-center gap-2 font-semibold text-gray-900">
                {n.title}
                <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-500">다음 단계</span>
              </p>
              <p className="mt-1 text-sm leading-relaxed text-gray-600">{n.text}</p>
            </li>
          ))}
        </ul>
      </section>

      <section className="flex flex-col gap-2 rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-xl font-bold text-gray-900">만든 사람들</h2>
        <p className="text-sm leading-relaxed text-gray-600">
          팀 <b className="text-gray-900">후속취재단</b> — 2026 뉴스빅데이터 해커톤(한국언론진흥재단) 출품작입니다. 잊힌 약속을 다시
          꺼내 보는 일을 시민과 기자, 공무원이 함께 할 수 있도록 만들었습니다.
        </p>
      </section>
    </div>
  );
}
