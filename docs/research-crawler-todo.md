# 네이버 증권 리서치 크롤러 TODO

> 설계 문서: [research-crawler-plan.md](./research-crawler-plan.md)

## 1단계: ES 인덱스 매핑

- [x] `pipeline/research_index_mapping.py` 작성
  - [x] `RESEARCH_INDEX` 상수 정의 (`naver-research-reports`)
  - [x] `RESEARCH_INDEX_MAPPING` 매핑 정의 (content, content_vector, metadata)
  - [x] metadata 필드: report_id, category, title, stock_name, stock_code, broker, date, views
  - [x] `create_research_index(es, delete_existing)` 함수 작성
- [x] ES 인덱스 생성 테스트 -- 전체 파이프라인 실행 시 자동 생성 확인

## 2단계: 크롤러

- [x] `pipeline/naver_research_crawler.py` 작성
  - [x] `ResearchReport` dataclass 정의
  - [x] `CATEGORIES` 딕셔너리 정의 (company, industry, economy, market, invest)
  - [x] `get_max_page(session, url)` -- 마지막 페이지 번호 추출
  - [x] `parse_report_list(session, category, page)` -- 한 페이지 파싱
    - [x] EUC-KR 인코딩 처리
    - [x] `<table class="type_1">` 파싱
    - [x] 종목분석 / 산업분석 등 카테고리별 컬럼 차이 처리
    - [x] 날짜 정규화 (`26.04.06` -> `2026-04-06`)
  - [x] `download_report_pdf(session, report, save_dir)` -- PDF 다운로드
    - [x] 파일 존재 시 스킵 (중복 방지)
    - [x] `time.sleep(0.3)` 레이트 리밋
  - [x] `crawl_reports(categories, max_pages, since_date, save_dir)` -- 통합 크롤링
    - [x] `since_date` 이전 리포트 만나면 조기 종료 (증분 수집)
- [x] 크롤링 단독 테스트 (`--max-pages 1 --crawl-only`) -- 29건 성공

## 3단계: 통합 파이프라인

- [x] `pipeline/research_main.py` 작성
  - [x] `index_research_documents(es, chunks, embeddings)` -- ES 적재 함수
  - [x] `check_already_indexed(es, report_id)` -- 중복 적재 방지
  - [x] `process_report(es, report, pdf_path, chunk_size, chunk_overlap)` -- 단일 리포트 처리
    - [x] `pdf_loader.load_pdf()` 재사용
    - [x] `chunker.chunk_documents()` 재사용
    - [x] 리서치 메타데이터 주입 (report_id, category, title, broker, date 등)
    - [x] `embedder.embed_texts()` 재사용
    - [x] ES bulk 적재
  - [x] CLI 인터페이스 (argparse)
    - [x] `--categories` (company / industry / economy / market / invest / all)
    - [x] `--max-pages`
    - [x] `--since`
    - [x] `--chunk-size`, `--chunk-overlap`
    - [x] `--recreate-index`
    - [x] `--crawl-only`, `--index-only`
    - [x] `--data-dir`
  - [x] 에러 핸들링 (파일별 try-except, 실패 목록 출력)
  - [x] 결과 요약 출력 (성공/실패 건수, ES 총 문서 수)

## 4단계: 테스트

- [x] 크롤링 테스트: `--max-pages 1 --crawl-only` -- 29건 PDF 다운로드 성공
- [x] 중복 다운로드 방지 확인: 재실행 시 PDF 스킵 정상 동작
- [x] PDF 파싱 확인: 세아제강 리포트 5페이지, 텍스트 정상 추출
- [x] 전체 파이프라인: 29개 리포트 -> 683건 청크 ES 적재 완료
- [x] ES 중복 적재 방지 확인: 재실행 시 29건 모두 "이미 적재됨, 건너뜀"
- [x] 증분 수집 확인: `--since 2026-04-06`으로 2페이지에서 04-03 리포트 만나 조기 중단 정상

## 5단계: 스케줄링

- [x] `agent-back/scripts/crawl_research.sh` 쉘 스크립트 작성
- [x] 로그 파일 출력 설정 (`agent-back/logs/crawl_research_YYYYMMDD_HHMMSS.log`)
- [ ] cron 등록 (매일 08:00 실행) -- 필요 시 `crontab -e`로 등록:
  ```
  0 8 * * * /Users/doo/Desktop/project/agent-study/agent-back/scripts/crawl_research.sh
  ```

## 6단계: agent-back 연동

- [x] `pyproject.toml`에 `elasticsearch` 의존성 추가
- [x] `app/core/config.py`에 ES_URL, ES_USER, ES_PASSWORD 설정 추가
- [x] `app/agents/tools.py`에 `search_research_reports` Tool 추가
- [x] ES hybrid 검색 구현 (BM25 + Vector, OpenAIEmbeddings 사용)
- [x] `stock_agent.py` tools 리스트에 등록
- [x] `prompts.py` 시스템 프롬프트에 리서치 분석 역할 및 도구 설명 추가
- [x] `.env`에 ES 접속 정보 추가
- [x] 검색 테스트 -- "세아제강 실적 전망" 검색 시 한화투자증권 리포트 정상 반환
