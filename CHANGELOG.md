# Workhub 개발 변경 이력

기준: 2026-11-09 · 시간 Asia/Seoul. 과거 버전은 당시 화면/문서에서 확인된 v0.5를 기준으로 하며, 그 이전 커밋에 임의의 의미 버전을 소급 부여하지 않는다. 아래 Git 커밋은 코드 변경 근거이며 실제 배포 성공을 뜻하지 않는다. 운영 검증 근거는 DEVELOPMENT_STATUS.md의 deploy ID/확인 기록을 함께 본다.

## v0.6.0 — 업무·일정 통합 및 정기 보고 (운영 확인 완료)

- 업무 종류와 사용자 정의, 보고 업무/간단 일정, 실제 수행시간·장소·근무 형태·고객사·예상시간·다음 조치.
- 대표 직원 업무 직권과 계정 운영 권한 분리, 본인 수행 업무 직권 승인 차단.
- 보고 업무의 반복 템플릿·독립 회차·미래 예정·중지·중복 방지·사용중지 담당자 확인.
- 주간/기간 보고·기존 업무 연결·제출 버전·팀장 취합·지정 검토자 승인/보완, 비공개 저장 첨부.
- 본인 수행 업무 iCal 구독 주소 발급/폐기, 최소정보·권한 범위·사용중지 차단.
- 신규 마이그레이션: tasks 0018~0021. 기존 인증정보·업무·개인 보드 삭제 없음.
- 검증: 전체 247개 자동 검사 통과. 정적 파일 생성·마이그레이션 정합성 확인. 배포 상태는 releases/v0.6.0.md에 실제 소스/배포 ID로 기록한다.
- 배포: 최종 소스 [0b729a7](https://github.com/gunsystems/workhub/commit/0b729a7170118f7ce773a41f6daa89807668184a), Render dep-dbokljpsrm7s73c9d6u0 LIVE (2026-11-09 13:24:39 KST). 서버 정상 응답과 새 화면, 기존 관리자·직원·업무 보존 확인. 후속 관련 검사 42개 통과.
- 제한: 템플릿 이후 회차 일괄 수정은 중지 후 새 업무에서 재설정. 휴일 이동 자동화, 반복 미래회차의 메인 달력 통합, 상세 시간표 보기 및 보고 의무 템플릿 기반 미제출 판정은 후속 개선. 하이웍스 실제 최초 구독과 공급자 갱신 주기는 별도 확인.

## v0.5 — 기존 운영 기능 누적 이력

| 구간 (한국 날짜) | 주요 변경 | 근거 커밋 | 검증 기록 |
|---|---|---|---|
| 2026-11-06 · 초기 기반 | 캘린더·역할/부서 권한·진행보고/검토·프로젝트·업무접수·조치 필요 | 569b9721, 077f9ec4, 27bf9424, 7be6e912 | 이후 누적 회귀 검사에 포함 |
| 2026-11-07 · 인증/메일 | 회사 이메일 초대·인증·OTP, 하이웍스 HTTPS 발송, HTML 초대/재설정 메일, HTTPS CSRF 수정 | cc036d6d, 78776237, 0679c97d, 94d16246, 14a8f42f | 당시 76/81/122개 등 커밋 메시지 및 개발 기록 참고 |
| 2026-11-07 · 계정 운영 | 직원 중지/재활성화·이력 보존 삭제, 관리자 지원 OTP 복구, 관리자 역할 분리 | 2ca8e3eb, ae60bb47 | 당시 93/117개 통과 기록 |
| 2026-11-07 · v0.5 | 공동/개별 배정, 순차·병렬 결재, 회수/보완/인계/직권, 비공개 개인 보드 | 7b1c9212 | 147개 통과 기록, d0256e19 운영 확인 기록 |
| 2026-11-08 · 개인 일정 | 태그·중요도·반복 날짜/시간·알림·Google 선택 전송, 보드 설정 취소/정렬 | 49a5ea33, a4e801f7, 1a77631d | 커밋별 자동검사 및 운영 확인 기록 |
| 2026-11-08 · 브랜드/도메인/도움말 | GUNSYSTEMS/Workhub 표기, 회사 도메인, 페이지별 시각 도움말, 명확한 Google 전송 표현 | 73aa8d4a, cfe7895b, aef9af18, a45b8835, c4e7c1a1, 20f40459 | DEVELOPMENT_STATUS.md 및 기존 증거 이미지 |
| 2026-11-08~09 · 조직/접근 | 팀·전사 현황/밀집 캘린더, 하이웍스 직원/메신저, 인증된 직원 자동 연결, 회사 재직 자격 검사 | 90db3c1d, 0255f5a1, 25883e92, ebb1d8ea | 당시 자동검사 및 운영 연동 기록 |
| 2026-11-09 · 직원 관리 | 실제 하이웍스 직원 선택, 정보 등록/초대 분리, 직속 상위자/임원 권한 표시, 부서 보존 | 8635960f, 6f61fc31, 07795c43, 0351e14d, 5e6fb732 | 225개 및 직원 UI 관련 검사 기록 |
| 2026-11-09 · UI/개발 기준 | 관리자 정렬·필터 규격, 초안 안내 수정, 외부 사례·하이웍스 일정 검토·최종 개발 범위 | e9f033dc, 7664440e, 8ea8699d | 기존 코드 227개 통과; 문서 변경과 배포 상태 구분 |

## 커밋별 원본 목록

공개 Git 변경만 수록한다. 토큰·비밀번호·초대 URL·직원 개인 데이터는 이력 문서에 기록하지 않는다.

| 한국 시각 | 커밋 | 변경 내용 |
|---|---|---|
| 2026-11-09 12:44:31 | [8ea8699d](https://github.com/gunsystems/workhub/commit/8ea8699dacea2f086c7a7895510e56c8293cb846) | docs: finalize unified work, recurring reporting and calendar development scope [skip render] |
| 2026-11-09 12:37:34 | [7664440e](https://github.com/gunsystems/workhub/commit/7664440e625f8d9742b8ad8098d60df59855639a) | Correct draft visibility guidance to match the existing creator-only access policy |
| 2026-11-09 12:28:22 | [ad7b066e](https://github.com/gunsystems/workhub/commit/ad7b066ebd00afc15e17fabf2956fbff2c2a75aa) | Record official external workflow patterns, Workhub design gaps and prioritized recommendations [skip render] |
| 2026-11-09 12:11:29 | [a4373ea4](https://github.com/gunsystems/workhub/commit/a4373ea45c27e1b0bc7f09305ae242bc6c41031b) | Record requested recurring report updates and official Hiworks calendar subscription findings [skip render] |
| 2026-11-09 10:48:29 | [e9f033dc](https://github.com/gunsystems/workhub/commit/e9f033dcae6688328f5c64ada9b2f0e9084255d8) | Keep work list filters aligned in a responsive bounded grid |
| 2026-11-09 10:45:54 | [a734c7cb](https://github.com/gunsystems/workhub/commit/a734c7cb9c30092ff33668ee99b65280765b08f9) | Unify search inputs, filter selects and toolbar buttons across work lists |
| 2026-11-09 10:43:59 | [8d555f36](https://github.com/gunsystems/workhub/commit/8d555f36020050e54f2ae53f2865f927a92d7046) | Keep administrator forms left aligned within the standard content width |
| 2026-11-09 10:39:11 | [3e25c482](https://github.com/gunsystems/workhub/commit/3e25c4826eba41e8e595b1a28b9e18102acaf59f) | Align administrator account content with the shared workspace layout |
| 2026-11-09 09:53:22 | [5e6fb732](https://github.com/gunsystems/workhub/commit/5e6fb732b2bd40c605200c7652be15b0c336121e) | Preserve the existing department when editing employee information |
| 2026-11-09 09:50:59 | [0351e14d](https://github.com/gunsystems/workhub/commit/0351e14d330d0938135935fb1a3486de3783f575) | Show effective executive business authority consistently across staff forms and employee displays |
| 2026-11-09 09:43:52 | [f30df86f](https://github.com/gunsystems/workhub/commit/f30df86f4de2917774a549dfb0e31f867e00ff18) | Record verified staff directory release and approved fixture cleanup [skip render] |
| 2026-11-09 09:39:50 | [f3998644](https://github.com/gunsystems/workhub/commit/f3998644558c9a2aba86c7bd579e6e497524b44e) | Clarify information-only registration and require explicit review of seed feedback cleanup |
| 2026-11-09 09:35:47 | [07795c43](https://github.com/gunsystems/workhub/commit/07795c434658832227bd9d2efbc8bac6a478d742) | Manage Workhub employees from the Hiworks directory with separate registration and invitation actions |
| 2026-11-09 09:21:07 | [6f61fc31](https://github.com/gunsystems/workhub/commit/6f61fc3194501317e968cf6ee7d5a9af719d6443) | Limit direct supervisor choices to team leaders, heads and executives |
| 2026-11-09 09:19:00 | [5dcf6289](https://github.com/gunsystems/workhub/commit/5dcf6289489c74cc804870933b63144ca8342963) | Only offer onboarded active employees as direct supervisors |
| 2026-11-09 09:15:09 | [c85251c9](https://github.com/gunsystems/workhub/commit/c85251c9fe3e1f9ceb4d0fbfd03ef7648f0b2dad) | Keep employee search compact and match existing form styling |
| 2026-11-09 09:12:42 | [8635960f](https://github.com/gunsystems/workhub/commit/8635960f6c91ed97b50ed92f881a513bacdf39aa) | Select verified Hiworks employees when sending staff invitations |
| 2026-11-09 08:35:23 | [c0d5cba1](https://github.com/gunsystems/workhub/commit/c0d5cba199b441b6a7946c9c266b878d9009d96b) | Use clearer Korean copy for safe request retry screen |
| 2026-11-09 08:05:12 | [6a814c8f](https://github.com/gunsystems/workhub/commit/6a814c8f4aef63a068a26be561d66e0ea1750f77) | Record verified production Hiworks employee access policy [skip render] |
| 2026-11-09 08:01:41 | [ebb1d8ea](https://github.com/gunsystems/workhub/commit/ebb1d8ea97326d25d58399092cff8f1bcb512765) | Restrict employee access to recently verified Hiworks membership and enforce OTP on account routes |
| 2026-11-08 22:48:37 | [b1aba312](https://github.com/gunsystems/workhub/commit/b1aba312308805bedab56b21a926bba5454cbb85) | Record automatic Hiworks linking and native staff UI verification [skip render] |
| 2026-11-08 22:44:42 | [25883e92](https://github.com/gunsystems/workhub/commit/25883e92d3d5f4fb43a47719686caadef1227408) | Automatically link verified invited employees to Hiworks and unify employee administration UI |
| 2026-11-08 22:20:37 | [3381dc69](https://github.com/gunsystems/workhub/commit/3381dc691e9c7468134791293492eb2619027c45) | Record Hiworks matching fix and outbound IP allowlist requirement [skip render] |
| 2026-11-08 22:14:57 | [0255f5a1](https://github.com/gunsystems/workhub/commit/0255f5a14123e8181bf916f1e6d548cf19d4cdf7) | Match Hiworks employees by office mail ID instead of shared contact email |
| 2026-11-08 21:51:15 | [5046db87](https://github.com/gunsystems/workhub/commit/5046db871745699cd025a4efd1a7d57ce99618e6) | Record verified priority release and remaining Hiworks secret setup [skip render] |
| 2026-11-08 21:47:06 | [90db3c1d](https://github.com/gunsystems/workhub/commit/90db3c1de655ec3d9c340e9d28563da4fa7881a3) | Strengthen team oversight and calendar views; add reviewed Hiworks staff links and notification outbox |
| 2026-11-08 21:15:56 | [d528f29a](https://github.com/gunsystems/workhub/commit/d528f29a6a04a5581d81e49e2692fb06e6190098) | Document reference-led roadmap and Hiworks integration review [skip render] |
| 2026-11-08 20:57:58 | [74ad5da2](https://github.com/gunsystems/workhub/commit/74ad5da210e386d8d1b1430379b77e3b704e439f) | Record work status naming and employee identity display [skip render] |
| 2026-11-08 20:53:34 | [20f40459](https://github.com/gunsystems/workhub/commit/20f4045902b44dfaa42742df94b0a27b205be28e) | Rename official work to work status and show employee identity in header |
| 2026-11-08 20:47:48 | [86d363eb](https://github.com/gunsystems/workhub/commit/86d363eb50b23e86fd6948a117c82bba6c571864) | Document company-domain cutover and verified employee login [skip render] |
| 2026-11-08 20:43:33 | [c4e7c1a1](https://github.com/gunsystems/workhub/commit/c4e7c1a176d3f0e6a08094c660f03f8d38d47b65) | Route legacy Workhub links safely to the company domain |
| 2026-11-08 20:34:37 | [a45b8835](https://github.com/gunsystems/workhub/commit/a45b88358df2c73a1a0ff719967f98ad28f37654) | Place contextual help at the far right of the page header |
| 2026-11-08 20:32:23 | [f80e8ef3](https://github.com/gunsystems/workhub/commit/f80e8ef3029bc076c55988f16062a4977bfb2023) | Record verified visual help and Google registration release [skip render] |
| 2026-11-08 20:28:19 | [aef9af18](https://github.com/gunsystems/workhub/commit/aef9af18e5e5c6a4812fb12ea2ebbe801cb0a9f1) | Add contextual visual help and clarify per-task Google Calendar registration |
| 2026-11-08 20:09:32 | [e58f91fd](https://github.com/gunsystems/workhub/commit/e58f91fd3a2270e6232fde0b1c3c828769997060) | Preserve white text on primary action links in detail panels |
| 2026-11-08 18:03:37 | [39b859a2](https://github.com/gunsystems/workhub/commit/39b859a2d9df61f57db2777605cd4b88a4f4ec45) | Record verified administrator layout and balanced branding [skip render] |
| 2026-11-08 18:00:43 | [cfe7895b](https://github.com/gunsystems/workhub/commit/cfe7895b0a71a0cb291e5d7930aede36084e0029) | Align administrator account form and balance Workhub product typography |
| 2026-11-08 17:55:31 | [dbda2183](https://github.com/gunsystems/workhub/commit/dbda218343c3755246d59200e0a67891acbb3343) | Record verified company and product branding [skip render] |
| 2026-11-08 17:53:46 | [4f359cb9](https://github.com/gunsystems/workhub/commit/4f359cb939273413fcbcc84d42bff56dabe46d5f) | Keep company name prominent and Workhub subtitle compact |
| 2026-11-08 17:51:38 | [39a23112](https://github.com/gunsystems/workhub/commit/39a2311240db5dc3408a248a59e557a49d1c62fa) | Add Workhub product subtitle beneath GUNSYSTEMS company wordmark |
| 2026-11-08 17:48:34 | [6039e917](https://github.com/gunsystems/workhub/commit/6039e91704fdfa6e366adf3b0a2ca221accc6777) | Record company branding release and DNS certificate dependency [skip render] |
| 2026-11-08 17:46:35 | [73aa8d4a](https://github.com/gunsystems/workhub/commit/73aa8d4a7e11ed80088ce8ebf90edfdc8ceefee4) | Replace temporary Workhub mark with GUNSYSTEMS company wordmark |
| 2026-11-08 17:38:44 | [3575d981](https://github.com/gunsystems/workhub/commit/3575d9814409f7fc9e55ff1c910f05f91ffde54a) | Use consistent bold priority borders for all personal cards |
| 2026-11-08 17:36:23 | [6a3a33f1](https://github.com/gunsystems/workhub/commit/6a3a33f130eabacc7fb74f5bfca1390fe476af60) | Record urgent styling release and pending company DNS verification [skip render] |
| 2026-11-08 17:31:32 | [0e47fedb](https://github.com/gunsystems/workhub/commit/0e47fedb2ca6c773417870dbea58a2c4e671823e) | Highlight urgent personal cards in bold red and prepare company hostname |
| 2026-11-08 10:35:17 | [9a42de08](https://github.com/gunsystems/workhub/commit/9a42de08d447d8415f68cb424e876f046841323a) | Record verified board settings and footer fix [skip render] |
| 2026-11-08 10:32:40 | [1a77631d](https://github.com/gunsystems/workhub/commit/1a77631d712352cbbe35503b9c58744761a3e077) | Fix board settings dismissal and align calendar action and footer spacing |
| 2026-11-08 10:27:29 | [ede08323](https://github.com/gunsystems/workhub/commit/ede083238bf632ddff72d4b3e56872efcabe7749) | Record verified timed scheduling release and pending OAuth setup [skip render] |
| 2026-11-08 10:24:44 | [a4e801f7](https://github.com/gunsystems/workhub/commit/a4e801f71925f7c884ff88083f8166880f493b66) | Add private tags, timed recurrence and opt-in Google Calendar integration |
| 2026-11-08 00:17:48 | [a9a291d1](https://github.com/gunsystems/workhub/commit/a9a291d154c529922c62a09789c49f947203700e) | Record verified navigation and personal card design release [skip render] |
| 2026-11-08 00:13:54 | [33e9a5e2](https://github.com/gunsystems/workhub/commit/33e9a5e2d5e8c54b6c561f08ae0388e58ba39f3e) | Refine laptop navigation spacing and anchor card menus near their trigger |
| 2026-11-08 00:07:34 | [49a5ea33](https://github.com/gunsystems/workhub/commit/49a5ea33a1fdb957dd308efef607227871968221) | Simplify navigation and upgrade personal board cards |
| 2026-11-07 23:27:34 | [d0256e19](https://github.com/gunsystems/workhub/commit/d0256e1962f24b08842097249a6a8c705b796cec) | Record verified live v0.5 release and preserved owner access [skip render] |
| 2026-11-07 23:23:17 | [7b1c9212](https://github.com/gunsystems/workhub/commit/7b1c9212c3f3b47592552177fe19f4d9036b5611) | Add official collaboration, explicit approval stages and private personal boards |
| 2026-11-07 22:32:36 | [d6dc2e2f](https://github.com/gunsystems/workhub/commit/d6dc2e2ff3c7e26c2aa0a34d2fe9041ef0ef5ad7) | Style password reset request, new password and expired-link screens |
| 2026-11-07 22:25:29 | [e6d0014f](https://github.com/gunsystems/workhub/commit/e6d0014fc8af3d1d8d7c8eab4b2923c78adf4ed2) | Record live confirmation popup, reset email design and Lee test deletion [skip render] |
| 2026-11-07 22:22:26 | [14a8f42f](https://github.com/gunsystems/workhub/commit/14a8f42fa9e646045dc5418f72b20ccf34618ec1) | Confirm employee deletion in a popup and brand password reset emails |
| 2026-11-07 22:11:10 | [5330c996](https://github.com/gunsystems/workhub/commit/5330c996c9728d3157839ddc45b2d0c77b534f04) | Record live OTP recovery release and Lee test reset [skip render] |
| 2026-11-07 22:08:19 | [ae60bb47](https://github.com/gunsystems/workhub/commit/ae60bb47f5370b5e6591368c4d5a2b432890ef4b) | Replace employee recovery codes with administrator-approved OTP restoration |
| 2026-11-07 21:37:05 | [84ab4432](https://github.com/gunsystems/workhub/commit/84ab4432ffb3237ae2967b2df6c4d66990002455) | Record completed test account reset and lifecycle deployment [skip render] |
| 2026-11-07 21:33:33 | [2ca8e3eb](https://github.com/gunsystems/workhub/commit/2ca8e3eb6c817d8ed7eede814468f4597ccaafd1) | Add employee suspension, reactivation and safe account deletion |
| 2026-11-07 21:17:40 | [83eec0d6](https://github.com/gunsystems/workhub/commit/83eec0d6ffef4b1b798740965e45fe18c8c795d0) | Improve authentication button layout and explain recovery codes |
| 2026-11-07 21:07:31 | [94d16246](https://github.com/gunsystems/workhub/commit/94d162465cf46d182466483a19ef1352ab7d9547) | Fix HTTPS CSRF failures on Workhub authentication forms |
| 2026-11-07 20:59:17 | [0679c97d](https://github.com/gunsystems/workhub/commit/0679c97d993aba00999e8cc72b2bab072572a4ab) | Style Workhub employee invitations with a branded HTML email |
| 2026-11-07 20:43:26 | [78776237](https://github.com/gunsystems/workhub/commit/78776237988809591416f1df399e5fb1386fc603) | Send Workhub authentication emails through Hiworks Office Token API |
| 2026-11-07 20:24:16 | [f6ce86c9](https://github.com/gunsystems/workhub/commit/f6ce86c97b967bee2893e2e26ea76d9cddbaf74a) | Merge pull request #1 from gunsystems/feature/employee-invitation-mfa |
| 2026-11-07 19:57:58 | [cc036d6d](https://github.com/gunsystems/workhub/commit/cc036d6db9f92a9b37ae255b7dc9a5980d8eefcf) | Add employee email invitations and mandatory OTP onboarding |
| 2026-11-06 22:38:24 | [7be6e912](https://github.com/gunsystems/workhub/commit/7be6e91258b271d2982d3a4481d07c5188dea25d) | Add work intake, project oversight, assignment acknowledgement and action center |
| 2026-11-06 17:17:23 | [27bf9424](https://github.com/gunsystems/workhub/commit/27bf94245e321946d7b71423c03becbb73be40e9) | Add personal and management dashboards with review workflow and performance scoring |
| 2026-11-06 16:56:12 | [077f9ec4](https://github.com/gunsystems/workhub/commit/077f9ec4db5214ae82d49259b3d740ba6556a5b0) | Enforce department access and employee roles |
| 2026-11-06 16:32:58 | [569b9721](https://github.com/gunsystems/workhub/commit/569b9721b37f6af30fd65c950a3f9a4720e487b5) | Build Workhub calendar and dedicated Render test deployment |

