-- 팀 편성 앱의 테이블 (Supabase Postgres). 이름은 모두 tf_ 로 시작한다.
-- 앱이 시작할 때 이 파일을 그대로 실행하므로(create ... if not exists), 앱이 쓰는 테이블과 같다.

create table if not exists tf_students (
    id            bigserial primary key,
    student_no    text not null unique,                 -- 학번 (유일). 관리자는 'admin'
    name          text not null,
    main_role     text check (main_role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너',
                                            '테스터', '보고서 작성자', '발표자')),  -- 관리자는 null
    password_hash text not null,                        -- bcrypt 해시 (원래 비밀번호는 저장하지 않음)
    is_admin      boolean not null default false
);

create table if not exists tf_student_sub_roles (
    student_id bigint not null references tf_students(id) on delete cascade,
    role       text not null check (role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너',
                                             '테스터', '보고서 작성자', '발표자')),
    primary key (student_id, role)
);

create table if not exists tf_teams (
    id   int primary key,                               -- 팀 번호 (팀1 -> 1)
    name text not null unique
);

-- 팀원. (team_id, slot_no) 기본 키와 student_id 유일성 제약이
-- "마지막 자리에 두 명이 동시에 들어가면 먼저 도착한 학생만 기록"과
-- "한 학생은 한 팀에만 소속"을 DB에서 지킨다. slot_no 검사로 다섯 번째 자리를 막는다.
create table if not exists tf_members (
    team_id    int not null references tf_teams(id) on delete cascade,
    slot_no    int not null check (slot_no between 1 and 4),
    student_id bigint not null unique references tf_students(id) on delete cascade,
    joined_at  timestamptz not null default now(),
    primary key (team_id, slot_no)
);

create table if not exists tf_applications (
    id         bigserial primary key,
    team_id    int not null references tf_teams(id) on delete cascade,
    student_id bigint not null references tf_students(id) on delete cascade,
    status     text not null default '대기' check (status in ('대기', '승인', '거절')),
    reason     text,
    created_at timestamptz not null default now(),
    decided_at timestamptz
);

-- 한 학생은 결과를 기다리는 지원을 하나만 가질 수 있다.
create unique index if not exists tf_applications_one_pending
    on tf_applications (student_id) where status = '대기';

create table if not exists tf_votes (
    application_id bigint not null references tf_applications(id) on delete cascade,
    voter_id       bigint not null references tf_students(id) on delete cascade,
    vote           text not null check (vote in ('찬성', '거부')),
    created_at     timestamptz not null default now(),
    primary key (application_id, voter_id)              -- 한 번 투표하면 바꿀 수 없음
);

create table if not exists tf_settings (
    key   text primary key,                             -- closed, proposal, assign_status, keepalive ...
    value text not null
);

create table if not exists tf_ai_logs (
    id          bigserial primary key,
    created_at  timestamptz not null default now(),
    attempt     int not null,                           -- 1차, 2차, 3차 (0 = 앱이 스스로 배정)
    model       text not null,
    outcome     text not null,                          -- 성공 / 시간 초과 / 오류 / 빈 응답 / 검사 실패 ...
    detail      text,
    elapsed_sec numeric(7, 1)
);

-- Supabase의 공개 REST API(anon 키)로 테이블을 읽지 못하게 막는다.
-- 앱은 DATABASE_URL(테이블 주인 계정)로 접속하므로 영향이 없다.
alter table tf_students          enable row level security;
alter table tf_student_sub_roles enable row level security;
alter table tf_teams             enable row level security;
alter table tf_members           enable row level security;
alter table tf_applications      enable row level security;
alter table tf_votes             enable row level security;
alter table tf_settings          enable row level security;
alter table tf_ai_logs           enable row level security;
