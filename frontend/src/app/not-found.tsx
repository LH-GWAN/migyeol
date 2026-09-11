import Link from "next/link";

export default function NotFound() {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-10 text-center">
      <p className="text-sm font-semibold text-gray-500">404</p>
      <h1 className="mt-1 text-xl font-bold text-gray-900">페이지 또는 사건을 찾을 수 없습니다</h1>
      <p className="mt-2 text-sm text-gray-600">주소가 잘못되었거나 해당 사건이 삭제되었을 수 있습니다.</p>
      <Link href="/" className="mt-5 inline-block rounded-md bg-gray-900 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-700">
        목록으로 돌아가기
      </Link>
    </div>
  );
}
