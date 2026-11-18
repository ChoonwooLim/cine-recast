# 명령별 참고

`cine-recast`의 모든 명령을 작업 순서대로 적었다. 명령마다 읽는 파일, 쓰는 파일, 출력, 거부되는 경우가 있다. 예시는 SKILL.md와 같이 두 변수를 쓴다.

```powershell
$cr = "$HOME/.cine-recast/bin/cine-recast.ps1"
$p = "$env:USERPROFILE\Videos\cine-recast\my-project"
```

- 첫 위치 인자는 항상 프로젝트 폴더다.
- 성공하면 종료 코드 0, 거부되거나 입력이 틀리면 2이고 이유가 `error:` 줄로 나온다. 1은 도구의 결함이다(`error: unexpected failure (...); this is a defect in cine-recast` 한 줄). 환경 변수 `CINE_RECAST_DEBUG=1`을 두면 그 뒤에 추적 정보가 붙는다.
- 프로젝트의 JSON 파일(`project.json`, `analysis/video.json`, `analysis/cuts.json`, `casting.json`, `models.json`, `plan.json`, `assets.json`, `sounds.json`, `out/soundtrack.json`)은 UTF-8로 읽고, 맨 앞의 BOM은 받아들인다. UTF-8이 아니거나 JSON이 깨졌으면 `error: casting.json is not valid JSON: ... at line 3, column 5`처럼 파일 이름(프로젝트 폴더 기준)과 줄과 칸이 나오고, 꼭 있어야 할 키가 없거나 맨 위가 객체가 아니면 파일 이름과 키가 나온다.
- `plan show`, `jobs list`, `jobs next`, `assets list`, `assets next`, `sound list`, `sound next`, `takes cuts`, `qc`, `audit`은 표준 출력에 JSON을 쓴다.
- 예시의 id, 숫자, 경로는 예시다.

## 프로젝트 폴더 구조

오른쪽은 그 파일을 쓰는 명령이다. 어떤 파일이 있는지 보면 프로젝트가 어느 단계까지 왔는지 알 수 있다.

```text
<프로젝트>/
  project.json                      init, rights set
  reference/source.mp4              init (참조 영상의 사본)
  analysis/
    video.json, cuts.json           analyze
    audio.json                      analyze (원본 소리의 타이밍: 프레임마다의 음량과 소리가 터지는 프레임)
    shots/0001/ref.mp4              analyze (화면 영역만 잘라 낸 숏 클립)
    shots/0001/sheet.json           analyze (숏 시트)
    shots/0001/stills/first.png, mid.png, last.png
    review/cut_0030.png, shot_0001.png      cuts review
    units/u01/ref.mp4               plan build, plan unit (단위의 참조 클립. 짧은 edit 단위는 끝에 마지막 프레임을 이어 붙인다)
  casting.template.json             cast template
  casting.json                      에이전트가 쓴다. assets use가 swaps를 고친다
  models.json                       에이전트가 쓴다
  assets.json                       assets add, set-cost, next, done, fail
  assets/hero/a0001_1.png           assets done (생성된 교체 자료)
  plan.json                         plan build, plan unit, takes select, takes confirm. 프롬프트는 에이전트가 쓴다
  plans/plan-0001.json              plan build --replace가 보관한 이전 계획
  sounds.json                       sound add, set-cost, next, done, fail, set
  sounds/s0001.wav                  sound done (생성된 소리)
  ledger.jsonl                      budget set, approve, jobs next, jobs done, jobs fail, assets next, assets done,
                                    assets fail, sound next, sound done, sound fail
  ledger.lock                       장부를 쓰는 명령이 쓰는 동안 잡는 잠금 파일(지우지 않아도 된다)
  takes/u01/t001/raw.mp4, meta.json jobs done
  takes/u01/t001/shots/0001.mov, conform.json     conform
  takes/u01/t001/scores.json        qc
  takes/u01/t001/review.mp4, review.jpg           takes review (원본이 들어 있는 검토용 파일)
  out/preview.mp4                   assemble --preview
  out/soundtrack.wav, soundtrack.json             sound mix
  out/master.mov, final.mp4         assemble
  out/compare.mp4                   compare
  out/report.md                     report
```

`ledger.jsonl`, `ledger.lock`, `assets/`, `sounds/`, `takes/`, `plans/`는 도구만 쓴다. 에이전트가 자기 용도로 만들어도 되는 폴더는 `swap/`(사용자가 준 교체 이미지), `downloads/`(생성 결과를 내려받는 곳), `review/`(검수용 스틸)다.

## 명령

### init

프로젝트 폴더를 만들고 참조 영상을 그 안으로 복사한다.

```powershell
& $cr init $p --reference "D:\footage\reference.mp4" --name my-project
```

- 쓰는 것: `project.json`, `reference/source.<확장자>`.
- 출력: `project created: <폴더>`.
- 무엇이든 만들기 전에 참조 영상을 ffprobe로 읽어 본다. 거부되면 프로젝트 폴더는 생기지 않는다. ffprobe가 PATH에 없으면 영상 탓이 아니므로 `ffprobe was not found on PATH`가 그대로 나온다.
- 거부: 참조 영상 파일이 없다. 프로젝트 폴더가 이미 있고 비어 있지 않다. 참조 영상을 ffprobe가 읽지 못하거나 영상 스트림이 없다. 컨테이너가 mp4/mov 계열, mkv/webm, avi, mpegts, mxf가 아니다. 회전 정보가 0이 아니다. 픽셀이 정사각형이 아니다(sample aspect ratio가 없거나 1:1이어야 한다). 프레임 속도가 가변이다(`avg_frame_rate`와 `r_frame_rate`가 2퍼센트 넘게 다르다). 이때 메시지가 할 일을 알려 준다. 회전 없는 정사각형 픽셀의 고정 프레임 속도 mp4로 다시 인코딩한 뒤 `init`을 다시 한다.

### rights set

권리 확인 한 항목의 답을 `project.json`에 기록한다. 다시 실행하면 그 항목의 답이 바뀐다.

```powershell
& $cr rights set $p --item likeness_consent --answer yes --note "the replacement is the user"
```

- `--item`: `reference_rights`, `likeness_consent`, `background_rights`.
- `--answer`: `yes`, `no`, `unknown`, `not_applicable`.
- 출력: `likeness_consent: yes`.
- 이 답은 `cast check`, `plan build`, `jobs next`, 그리고 참조 이미지가 있는 자료의 `assets add`, `assets set-cost`, `assets next`가 실행될 때마다 다시 읽힌다. 승인 뒤에 답이 바뀌어 검사를 통과하지 못하면 작업이 나가지 않는다.

### analyze

참조 영상의 화면 영역을 정하고, 컷을 찾고, 숏마다 폴더를 만든다.

```powershell
& $cr analyze $p
& $cr analyze $p --cuts "30,54"
& $cr analyze $p --picture "0,46,640,268"
```

- 쓰는 것: `analysis/video.json`(크기, 프레임 속도, 프레임 수, 화면 영역 `picture_box`와 그 출처 `picture_source`), `analysis/cuts.json`(컷 목록과 출처), `analysis/audio.json`(원본 소리의 타이밍, 아래 "파일 형식"), `analysis/shots/NNNN/`의 `ref.mp4`, `sheet.json`, `stills/`. 숏 시트의 `audio_hits`에는 `audio.json`의 `hits` 가운데 그 숏 안의 프레임이 오름차순으로 들어간다(원본에 소리가 없으면 빈 목록).
- 출력: `shots: 3  cuts: [30, 54]  picture: {'x': 0, 'y': 0, 'w': 384, 'h': 216}`.
- `--cuts`: 검출 대신 쓸 컷 전체. 새 숏이 시작하는 프레임 번호를 오름차순으로 적는다. 컷이 하나도 없는 영상은 `--cuts ""`.
- `--picture`: 검출 대신 쓸 화면 영역. 왼쪽 위 모서리의 x, y와 너비, 높이를 쉼표로 이어 적는다(원래 크기 프레임의 픽셀). 네 값 모두 짝수인 정수이고, 너비와 높이는 0보다 크며, 영역이 프레임 안에 있어야 한다. `--cuts`와 함께 쓸 수 있다. 생략하면 검출한다.
- `picture_source`: 검출한 영역이면 `detected`, `--picture`로 준 영역이면 `given`. 이 키가 없는 이전 프로젝트는 `detected`로 본다.
- 다시 실행하면 숏 폴더를 새로 만든다.
- 거부: `plan.json`이 있다. 계획이 생기면 컷과 화면 영역이 고정되므로, 바꾸려면 새 프로젝트 폴더에서 `init`부터 다시 한다(그때까지 만든 테이크는 넘어가지 않는다). 컷 번호가 오름차순이 아니거나 영상 밖이다. `--picture`가 위 조건에 맞지 않는다.

### cuts review

컷과 숏을 눈으로 검수할 이미지를 만든다.

```powershell
& $cr cuts review $p
```

- 쓰는 것: `analysis/review/cut_NNNN.png`(컷마다, 프레임 N-2부터 N+1까지 네 장), `analysis/review/shot_NNNN.png`(숏마다, 고르게 뽑은 여덟 장). 칸마다 프레임 번호가 적혀 있다.
- 출력: 만든 이미지의 경로를 한 줄에 하나씩.

### cast template

`casting.json`을 쓸 때 참고할 틀을 만든다. `casting.json`은 건드리지 않는다.

```powershell
& $cr cast template $p
```

- 쓰는 것: `casting.template.json`. 숏마다 번호, 시작과 끝 프레임, 스틸 세 장의 경로가 있고 `elements`와 `swaps`는 비어 있다.
- 출력: 그 파일의 경로.

### cast check

`casting.json`과 권리 답을 검사한다.

```powershell
& $cr cast check $p
```

- 출력: `casting ok`. 문제가 있으면 문제마다 한 줄씩 내고 종료 코드 2.
- 검사하는 것: 먼저 형식. 맨 위가 객체이고 `elements`와 `swaps`가 객체의 목록인지, 요소의 `id`, `kind`, `name`, `look`이 문자열이고 `shots`가 정수 목록인지, 교체의 `element`가 문자열이고 `images`가 문자열 목록인지. 형식이 틀린 곳도 문제 한 줄씩으로 나온다. 그다음 요소의 `id`(끝에 줄바꿈이 붙은 id도 거부), `kind`, `shots`, `look`. 교체표의 요소가 요소 목록에 있는지, 요소 하나에 교체 하나인지, 이미지 파일이 있고 `.jpg`, `.jpeg`, `.png`, `.webp`인지. 모든 문자열에 한자가 없는지.
- 권리: 인물을 교체하면 `likeness_consent`가 `yes`여야 한다. `not_applicable`은 인물 교체 이미지가 모두 이 프로젝트에서 참조 이미지 없이 생성한 파일일 때만 통과한다. 배경을 교체하면 `background_rights`에 답이 있어야 하고, `reference_rights`에는 언제나 답이 있어야 한다.

### models check

`models.json`을 검사한다. 형식은 generation-jobs.md에 있다.

```powershell
& $cr models check $p
```

- 출력: `models ok`. 문제가 있으면 문제마다 한 줄씩 내고 종료 코드 2.
- `kind`는 `video`, `image`, `audio`다. 소리 모델(`audio`)은 `id`, `min_seconds`, `max_seconds`(0보다 큰 수, 최대가 최소 이상), `read_at`만 검사한다. `plan build`, `plan unit`은 영상 모델만, `assets add`는 이미지 모델만, `sound add`는 소리 모델만 받는다.

### assets add

교체 이미지가 없는 요소에 이미지 생성 작업을 하나 계획한다.

```powershell
& $cr assets add $p --element hero --prompt "Full-body studio portrait of a fictional woman in her twenties, short black hair, olive field jacket, plain grey backdrop, soft even light." --model image_model_x --batch assets-1 --count 2
& $cr assets add $p --element sky_battle --prompt "Wide sunset sky over a rocky coast, low orange sun on the left, thin high clouds, no people, no text." --model image_model_x --batch assets-1 --aspect-ratio 16:9
```

- `--element`: `casting.json`의 요소 id. 교체표의 나머지가 아직 완성되지 않았어도 된다.
- `--model`: `models.json`의 이미지 모델 id. `--prompt`: 영어.
- `--count`: 한 번에 받을 후보 수, 1에서 4. 기본 1.
- `--aspect-ratio`: 모델이 제공하는 `W:H`. 생략하면 배경은 화면 영역에 가장 가까운 비율, 그 밖의 요소는 모델의 첫 비율.
- `--ref`: 참조 이미지. 여러 번 쓸 수 있고, 상대 경로는 프로젝트 폴더 기준이다. 인물에 `--ref`를 쓰면 그 결과는 가상 인물이 아니다.
- 참조 이미지가 있으면 권리 답을 읽는다. 요소의 종류는 자료를 만들 때 적힌 값이 아니라 그 순간의 `casting.json`에서 읽는다. 인물이면 `likeness_consent`가 `yes`여야 하고, 배경이면 `background_rights`에 답이 있어야 한다(배경 교체와 같은 규칙). `assets set-cost`와 `assets next`도 같은 검사를 다시 한다.
- 쓰는 것: `assets.json`. 출력: 자료 id(`a0001`).
- 거부: 요소가 없다. 프롬프트가 영어가 아니다. 모델이 이미지 모델이 아니다. 장수나 비율이 모델 범위 밖이다. 참조 이미지가 없거나 모델이 받는 수(`image_refs`)보다 많다. 참조 이미지가 있는데 위의 권리 답이 없다(실행할 `rights set` 명령이 함께 나온다).

### assets list

자료 작업과, 작업마다 실제로 보낼 `params`를 보여 준다. 가격을 물을 때 이 `params`를 쓴다.

```powershell
& $cr assets list $p
```

- 출력: JSON 목록. 항목마다 `id`, `element`, `kind`, `batch`, `state`(`planned`, `quoted`, `issued`, `done`, `failed`), `est_credits`, `params`(`model`, `prompt`, `aspect_ratio`, `count`, 모델에 해상도가 있으면 `resolution`), `files`(등록된 이미지).

### assets set-cost

자료 작업의 견적을 기록한다. 견적은 그 시점의 `params`와 참조 이미지에 묶인다.

```powershell
& $cr assets set-cost $p --asset a0001 --credits 6
```

- 출력: `a0001: quoted 6.0 credits`. 상태가 `quoted`가 된다.
- 내보내기 전에는 다시 실행해 견적을 바꿀 수 있다.
- 거부: 이미 내보낸 자료다. 크레딧이 0 이하다.

### assets next

묶음에서 견적이 있는 자료 작업을 장부에 올리고, 실행할 내용을 낸다.

```powershell
& $cr assets next $p --batch assets-1
```

- 출력: JSON 목록. 항목마다 `job_id`(자료 id), `element`, `tool`(`generate_image`), `params`, `medias`(참조 이미지의 절대 경로, 역할은 `image`). 내보낼 것이 없으면 `[]`.
- 한 번 내보낸 자료는 다시 나오지 않는다.
- 거부(종료 코드 2): 묶음에 승인 기록이 없다. 견적을 더하면 한도를 넘는다. 견적 뒤에 `params`나 참조 이미지가 바뀌었다. `assets add`의 검사를 통과하지 못한다(참조 이미지가 있는 인물 자료인데 그사이 `likeness_consent`가 `yes`가 아니게 된 경우 포함). 장부가 거부하기 전에 내보낸 자료가 있으면 그 목록은 그대로 출력된다.

### assets done

돌려받은 이미지를 등록하고 자료 작업을 끝난 것으로 기록한다.

```powershell
& $cr assets done $p --asset a0001 --provider-id 2b9d4c1e-7f3a-4e5b-8c6d-1a2b3c4d5e6f --credits 5.5 --file "$p\downloads\a0001_1.png" --file "$p\downloads\a0001_2.png"
```

- `--file`: 돌려받은 이미지마다 한 번씩. `--provider-id`: Higgsfield의 작업 id. `--credits`: 실제로 차감된 크레딧.
- 쓰는 것: `assets/<요소>/<자료 id>_<번호>.<확장자>`, `assets.json`, `ledger.jsonl`. 장부에 `done`으로 기록된 자료의 파일은 바꾸지 않는다. 장부에 `done`이 없는데 그 자료 id로 된 파일이 이미 있으면, 앞서 중간에 끊긴 `assets done`이 남긴 것이므로 지우고 이번에 준 파일을 저장한다. 끊긴 뒤에는 같은 명령을 같은 파일로 다시 하면 된다.
- 출력: `a0001: done, files assets/hero/a0001_1.png, assets/hero/a0001_2.png`.
- 거부: 내보낸 상태(`issued`)가 아니다. 파일이 없거나 이미지 형식이 아니다. 프로젝트의 `reference/`, `analysis/`, `takes/`, `out/`, `assets/` 안에 있는 파일이다(원본 스틸이나 다른 자료로 이미 등록된 이미지 같은 프로젝트 자료이지 생성 결과가 아니다).

### assets fail

실패한 자료 작업을 기록한다.

```powershell
& $cr assets fail $p --asset a0002 --reason "provider rejected the prompt" --credits 0
```

- `--credits`: 실패했는데도 차감된 크레딧. 기본 0.
- 출력: `a0002: failed`.

### assets use

생성된 이미지 한 장을 그 요소의 교체 자료로 `casting.json`에 등록한다.

```powershell
& $cr assets use $p --element hero --image assets/hero/a0001_2.png
```

- `--image`: 그 요소의 생성 파일 가운데 하나. 프로젝트 폴더 기준 경로다.
- 쓰는 것: `casting.json`의 `swaps`. 그 요소의 교체 항목이 `{"element", "images": [그 이미지], "note": "generated: a0001"}`로 바뀌거나 새로 들어간다.
- 출력: `hero: swap image assets/hero/a0001_2.png`.
- 거부: 그 요소의 생성 파일이 아니다(장부에 `done`으로 기록된 자료의 파일만 인정한다).

### plan build

숏을 생성 단위로 묶고, 단위마다 참조 클립과 작업 하나를 만든다.

```powershell
& $cr plan build $p --model seedance_2_5 --method edit --batch draft-1 --per-shot
& $cr plan build $p --model seedance_2_5 --method edit --batch draft-1
& $cr plan build $p --model seedance_2_5 --method regen --batch cmp-regen --max-unit-seconds 2 --replace
```

- `--method edit`: 단위의 참조 클립(역할 `video`)과 교체 이미지를 입력으로 삼는다. 모델의 `video_ref`가 `true`여야 한다. `--method regen`: 단위 첫 숏의 첫 스틸(역할 `image`)과 교체 이미지를 입력으로 삼는다.
- `--quality`: 처음 만드는 작업의 품질. 기본 `draft`.
- `--per-shot`: 숏마다 단위를 하나씩, 숏 순서대로 만든다. `edit`에서는 이것을 기본으로 쓴다. 실제 생성에서 모델은 숏이 여럿인 단위 안의 숏을 다른 화면으로 바꾸거나, 빼먹고 이웃 숏을 늘리기도 했다. 숏 여럿을 한 단위로 묶는 것은 고른 모델이 단위 안의 숏을 따로 지킨다는 것을 시험으로 확인한 경우에만 한다. `--max-unit-seconds`와 함께 쓰지 않는다.
- `--max-unit-seconds`: 단위 길이의 상한(초). 생략하면 모델의 `max_seconds`. 이어지는 숏을 합친 길이가 상한 이하인 동안 한 단위에 담는다.
- `--replace`: 계획이 이미 있을 때 다시 만든다. 이전 계획은 `plans/plan-NNNN.json`으로 보관되고, 새 단위와 작업은 지금까지 쓴 적 없는 id를 받는다.
- 쓰는 것: `plan.json`, `analysis/units/<단위>/ref.mp4`.
- 패딩: `edit` 단위의 길이(단위의 프레임 수를 원본의 프레임 속도로 나눈 값)가 모델의 `min_seconds`보다 짧으면, 참조 클립에 단위의 프레임을 넣고 그 뒤에 마지막 프레임의 사본을 이어 붙여 모두 `ceil(min_seconds * 프레임 속도)` 프레임으로 만든다(23.976fps에서 3초면 72프레임). 모델이 받는 원본 영상의 최소 길이를 채우기 위해서다. 붙인 수는 단위의 `pad_frames`에 적히고(붙이지 않았으면 0) 그 단위의 출력 줄 끝에 `  padded +66 frames`처럼 나온다. 길이가 충분한 단위와 `regen` 단위에는 붙이지 않는다. 붙인 프레임은 완성본에 들어가지 않는다. `conform`은 언제나 원본 숏의 프레임 수만큼 숏 클립을 만든다.
- 출력: 단위마다 한 줄과 마지막의 작업 줄. `--per-shot`으로 숏 셋(30, 24, 36프레임, 24fps)을 `min_seconds` 4인 모델로 계획하면 다음과 같다. 패딩하지 않은 단위의 줄은 `duration 4 s`에서 끝난다.

  ```text
  u01: shots [1]  frames 0-30  duration 4 s  padded +66 frames
  u02: shots [2]  frames 30-54  duration 4 s  padded +72 frames
  u03: shots [3]  frames 54-90  duration 4 s  padded +60 frames
  planned jobs: j0001, j0002, j0003 (batch draft-1)
  ```
- 단위의 `duration`은 단위 길이를 초로 올림한 값이고 모델의 `min_seconds`보다 작지 않다. 그래서 테이크가 단위보다 길 수 있으며, `conform`이 필요한 프레임만 쓴다.
- 거부: `cast check`나 `models check`를 통과하지 못한다. 영상 모델이 아니다. `edit`인데 모델이 참조 영상을 받지 않는다. 숏 하나가 상한보다 길다. 단위에 필요한 이미지가 모델의 `image_refs`보다 많다. `--per-shot`과 `--max-unit-seconds`를 함께 줬다. 계획이 있는데 `--replace`가 없다. 내보냈지만 끝나지 않은 작업이 있다(`jobs done`이나 `jobs fail`로 먼저 닫는다).

### plan unit

계획의 단위 하나를 지금 상태에 맞춘다. 실행할 때마다 단위의 입력을 지금의 교체표에서 다시 정하고, `--model`이나 `--method`를 주면 그 단위의 모델이나 방법을 바꾼다. 단위 id, 숏, 프레임, 프롬프트, `qc_skip`, 테이크, 고른 테이크, 사용자 확정(`confirmed`)은 그대로 남는다. 고른 테이크의 화면은 바뀌지 않기 때문이다. 계획 전체를 다시 만들지 않으므로 다른 단위는 건드리지 않는다.

```powershell
& $cr plan unit $p --unit u01
& $cr plan unit $p --unit u02 --model kling_video_edit
& $cr plan unit $p --unit u02 --model seedance_2_5 --method regen
```

- 출력: `u02: model kling_video_edit, method edit, duration 4 s, inputs: analysis/units/u02/ref.mp4, assets/hero/a0001_2.png`. 패딩한 단위면 줄 끝에 `  padded +72 frames`가 붙는다.
- 입력: `plan build`와 같은 규칙으로 정한다. 맨 앞에 `edit`는 단위의 참조 클립, `regen`은 첫 숏의 첫 스틸이 오고, 이어서 단위의 숏에 나오는 요소의 교체 이미지가 요소 목록 순서로 온다. 한 요소의 `images`에 이미지가 여럿이면 적힌 순서대로 모두 들어간다. 이미지 입력 수가 모델의 `image_refs`보다 많으면 거부된다.
- `--model`, `--method`: 생략하면 단위의 지금 값이다. 모델이나 방법이 바뀌면 `plan build`가 그 단위에 하는 일을 다시 한다. 길이(`duration`)는 단위 길이를 초로 올림한 값과 모델의 `min_seconds` 가운데 큰 값, 화면비는 모델의 비율 가운데 화면 영역에 가장 가까운 것, 단위의 기본 해상도는 이전과 같은 품질의 값(초안이면 첫 값, 본 생성이면 마지막 값), `pad_frames`는 새 모델의 `min_seconds`로 다시 계산한 값이다. 패딩이 바뀌면 단위의 참조 클립(`analysis/units/<단위>/ref.mp4`)을 다시 쓰고(패딩이 0으로 돌아가기도 한다), 바뀌지 않으면 참조 클립을 건드리지 않는다.
- 작업: 모델이나 방법이 바뀌면 그 단위에서 아직 내보내지 않은 작업(`planned`, `quoted`)은 견적이 지워지고 `planned`로 돌아간다. 견적은 다른 모델의 가격이기 때문이다. 입력만 바뀐 경우에는 견적이 남지만 견적이 묶인 입력과 다르므로 `jobs next`가 그 작업을 거부한다. 어느 경우든 가격을 다시 물어 `jobs set-cost`를 한다. 끝난 작업(`done`, `failed`)은 기록한 것을 모두 그대로 두고, 그 뒤에 `jobs add`로 더한 작업은 단위의 새 모델로 나간다.
- 쓰는 것: `plan.json`의 그 단위와 그 단위의 작업, 패딩이 바뀌었으면 `analysis/units/<단위>/ref.mp4`.
- 거부: 현재 계획에 없는 단위다. 그 단위에 내보냈지만 끝나지 않은 작업이 있다(`jobs done`이나 `jobs fail`로 먼저 닫는다). `cast check`를 통과하지 못한다. 모델이 `models.json`에 없거나 영상 모델이 아니다. `edit`인데 모델이 참조 영상을 받지 않는다. 단위가 모델의 `max_seconds`보다 길다. 이미지 입력 수가 모델의 `image_refs`보다 많다. 거부되면 아무것도 바뀌지 않는다.

교체 이미지는 프로젝트 도중에도 고칠 수 있고, 그때 이미 고른 테이크는 잃지 않는다.

1. `casting.json`의 `swaps[].images`를 고치거나, `assets use`로 새로 생성한 이미지를 등록한다.
2. `cast check`를 통과시킨다.
3. `plan check`가 입력이 다르다고 알리는 단위마다 `plan unit`을 실행한다. 그 요소가 나오는 단위가 모두 해당한다.
4. 고친 단위에 `jobs add`로 새 작업을 더하고 가격, 승인, 생성을 이어 간다.

`plan build --replace`는 모든 단위에 새 id를 주므로 이미 만든 테이크를 현재 계획에서 쓸 수 없게 된다. 계획 전체를 다시 짤 때만 쓴다.

### plan check

계획의 작업을 내보낼 수 있는지 검사한다.

```powershell
& $cr plan check $p
```

- 출력: `plan ok`. 문제가 있으면 문제마다 한 줄씩 내고 종료 코드 2.
- 검사하는 것: 단위의 프롬프트가 비어 있지 않은 영어인지, 입력 파일이 있는지, 길이와 화면비와 해상도가 모델 범위 안인지, 견적 뒤에 `params`나 입력이 바뀌지 않았는지.
- 형식: 단위 id가 `u`와 숫자(`u01`), 작업 id가 `j`와 숫자(`j0001`)인지, 단위의 `method`가 `edit`나 `regen`인지, `shots`가 비어 있지 않은 숏 번호 목록인지, `qc_skip`이 `camera`, `motion`, `color`, `flicker_ratio` 가운데 이름을 담은 목록인지, `pad_frames`가 있으면 0 이상의 정수인지(이 키가 없는 이전 계획은 통과한다), `confirmed`가 있으면 `take`(테이크 id), `by`, `at`, `note`만 가진 객체인지(없어도 통과한다). 단위 id는 폴더 이름이 되므로, 형식이 틀린 단위는 `conform`, `qc`, `assemble`, `report`도 거부한다.
- 입력: 단위의 `inputs`가 지금의 교체표와 방법으로 정해지는 입력(맨 앞에 `edit`는 단위의 참조 클립, `regen`은 첫 숏의 첫 스틸, 이어서 단위의 숏에 나오는 요소의 교체 이미지가 요소 목록 순서로)과 하나도 다르지 않은지. 빠지거나 더해지거나 순서나 경로나 역할이나 요소가 다르면 `plan unit <project> --unit u01`로 그 단위를 지금 교체표에 맞추라는 문제가 나온다(같은 문제에 적힌 `plan build --replace`는 모든 단위를 새 id로 다시 만드는 방법이다). 교체표가 `cast check`를 통과하지 못하면 입력을 비교할 수 없으므로 그것도 문제로 나온다.
- 모델: 단위마다 그 단위의 모델로 검사한다. 이미지 입력 수가 지금 `models.json`의 `image_refs` 이하인지, `edit`이면 모델의 `video_ref`가 `true`인지.

### plan show

`plan.json`을 그대로 보여 준다. 단위의 `shots`, `start`, `end`, `method`, `model`, `params`, `prompt`, `inputs`, `qc_skip`, `selected_take`, `pad_frames`, 사용자가 확정했으면 `confirmed`, 그리고 작업 목록이 있다.

```powershell
& $cr plan show $p
```

### jobs list

영상 작업과, 작업마다 실제로 보낼 `params`를 보여 준다. 가격을 물을 때 이 `params`를 쓴다.

```powershell
& $cr jobs list $p
```

- 출력: JSON 목록. 항목마다 `id`, `unit`, `batch`, `quality`, `state`, `est_credits`, `params`(`model`, `prompt`, `duration`, `aspect_ratio`, `resolution`).
- 내보낸 뒤의 작업은 내보낼 때 실제로 보낸 `params`가 나온다.
- 입력 파일은 여기에 없다. `plan show`의 `units[].inputs`에서 읽는다.

### jobs add

있는 단위에 작업을 하나 더한다. 재시도와 본 생성에 쓴다.

```powershell
& $cr jobs add $p --unit u01 --batch draft-1 --quality draft
& $cr jobs add $p --unit u01 --batch final-1 --quality final
```

- `--quality draft`는 `models.json`에 적힌 그 모델의 첫 해상도, `final`은 마지막 해상도로 나간다. 모델은 작업을 더한 단위의 지금 모델이다(`plan unit`으로 바꿨으면 바꾼 모델).
- 출력: 새 작업 id(`j0002`). 한 번 쓴 id는 다시 쓰이지 않는다.

### jobs set-cost

작업의 견적을 기록한다. 견적은 그 시점의 `params`(프롬프트 제외)와 입력 파일에 묶인다.

```powershell
& $cr jobs set-cost $p --job j0001 --credits 40
```

- 출력: `j0001: quoted 40.0 credits`. 상태가 `quoted`가 된다.
- 내보내기 전에는 다시 실행해 견적을 바꿀 수 있다.
- 거부: 이미 내보낸 작업이다. 크레딧이 0 이하다.

### approve

묶음의 승인 한도를 장부에 기록한다. 사용자가 승인한 범위 안에서만 실행한다(SKILL.md의 "비용 승인": 프로젝트 예산 안의 견적 합계, 사용자가 말한 금액, 또는 사용자가 비용을 묻지 말라고 했을 때의 견적 합계).

```powershell
& $cr approve $p --batch draft-1 --limit 80 --by owner --note "drafts for u01, one retry included"
```

- 쓰는 것: `ledger.jsonl`. 출력: 기록한 뒤의 묶음 상태. `approved batch draft-1: +80.0 credits, limit now 80.0, committed 0.0, remaining 80.0 (by owner)`(이번에 더한 한도, 지금 한도, 쓴 크레딧과 끝나지 않은 작업의 견적, 남은 한도). 프로젝트 예산이 있으면 둘째 줄에 `budget: limit 700.0, approved 80.0, remaining 620.0 (by owner)`(예산, 모든 묶음 한도의 합, 남은 예산)가 나온다.
- 같은 묶음에 다시 실행하면 한도가 더해진다. 추가 승인에는 추가분만 적는다.
- 거부: 장부가 손상돼 있다(`ledger line N is invalid`). 손상된 장부에는 아무것도 덧붙이지 않는다. 프로젝트 예산이 있는데 모든 묶음 한도의 합이 예산을 넘게 된다(`above the project budget`). 예산이 없으면 이 검사는 없다.

### budget set

프로젝트 예산을 장부에 기록한다. 사용자가 프로젝트 전체의 예산을 승인한 뒤에만 실행한다.

```powershell
& $cr budget set $p --limit 700 --by owner --note "whole project, approved in chat"
```

- 쓰는 것: `ledger.jsonl`에 `budget` 줄. 나중에 다시 실행하면 그 줄이 앞의 예산을 대신한다(올리거나 내릴 수 있다).
- 출력: `budget: limit 700.0, approved 0.0, remaining 700.0 (by owner)`.
- 예산이 있는 동안 `approve`는 모든 묶음 한도의 합이 예산을 넘게 되는 승인을 거부한다. 묶음의 한도와 `jobs next`, `assets next`, `sound next`의 검사는 그대로다.
- 거부: 금액이 0보다 큰 유한한 수가 아니다. `--by`가 비어 있다. 장부가 손상돼 있다. `budget` 줄도 다른 줄처럼 엄격히 읽으므로, 손상된 `budget` 줄은 장부를 읽는 모든 명령을 멈춘다.

### jobs next

묶음에서 견적이 있는 작업을 작업 번호 순서로 장부에 올리고, 실행할 내용을 낸다.

```powershell
& $cr jobs next $p --batch draft-1
```

- 출력: JSON 목록. 항목마다 `job_id`, `unit`, `tool`(`generate_video`), `params`, `medias`(입력 파일의 절대 경로와 역할 `video` 또는 `image`). 내보낼 것이 없으면 `[]`.
- 견적이 없는 작업(`planned`)은 나가지 않는다. 한 번 내보낸 작업은 다시 나오지 않는다.
- 내보내기 전에 하는 `plan check`는 이번에 내보낼 작업(그 묶음에서 상태가 `quoted`인 작업)과 그 단위만 검사한다. 시험하지 않는 단위의 프롬프트가 비어 있어도 나간다. `plan check` 명령은 모든 단위를 검사한다.
- 내보낸 작업은 끝날 때까지 견적만큼, 끝난 뒤에는 실제 사용액만큼 한도를 차지한다.
- 거부(종료 코드 2): 묶음에 승인 기록이 없다. 견적을 더하면 한도를 넘는다(`would exceed its approved limit`). `cast check`나 `plan check`를 통과하지 못한다. 장부가 거부한 작업에서 멈추며, 그 전에 내보낸 작업의 목록은 그대로 출력된다.

### jobs done

돌려받은 영상을 새 테이크로 보관하고 작업을 끝난 것으로 기록한다.

```powershell
& $cr jobs done $p --job j0001 --provider-id 7c2f0e1a-5b1d-4c7e-9a43-0d6f2b8e1c55 --credits 38 --file "$p\downloads\j0001.mp4"
```

- `--provider-id`: Higgsfield의 작업 id. `--credits`: 실제로 차감된 크레딧.
- 쓰는 것: `takes/<단위>/<테이크>/raw.<확장자>`, `meta.json`, `ledger.jsonl`, `plan.json`. 테이크 번호는 `t001`부터 올라가고 덮어쓰지 않는다.
- 출력: `j0001: done, unit u01 take t001`.
- 거부: 내보낸 상태(`issued`)가 아니다. 파일이 없다. 프로젝트의 `reference/`, `analysis/`, `takes/`, `out/`, `assets/` 안에 있는 파일이다(링크와 `..`을 따라간 실제 위치로 보고, 대소문자가 달라도 같은 폴더로 본다). 그런 파일은 프로젝트 자료이지 생성 결과가 아니다. 바이트가 프로젝트의 참조 영상 사본과 같은 파일이다(`init`에 준 원본 파일이 밖에 있어도 걸린다). 그것은 참조 영상이지 생성 결과가 아니다. 생성 작업에서 내려받은 파일만 등록한다.
- 손으로 고쳐 망가진 단위(형식이 틀린 단위 id, `shots`, `qc_skip`)도 `jobs done`을 멈춘다. 메시지가 무엇을 고칠지 알려 주고(`plan check`가 문제를 보여 준다), 그때는 아무것도 저장되지 않고 장부에도 적히지 않는다.

### jobs fail

실패한 작업을 기록한다.

```powershell
& $cr jobs fail $p --job j0002 --reason "provider error: content rejected" --credits 0
```

- `--credits`: 실패했는데도 차감된 크레딧. 기본 0.
- 출력: `j0002: failed`. 다시 하려면 `jobs add`로 새 작업을 만든다.

### audit

장부를 점검한다. 장부가 손상돼 다른 명령이 모두 멈췄을 때도 이 명령은 돌아간다.

```powershell
& $cr audit $p
```

- 출력: JSON. `ok`, `problems`, `batches`(묶음마다 `limit`, `committed`, `remaining`), `totals`(모든 묶음의 `limit`, `committed`, `remaining` 합계), `provider_ids`(끝난 작업의 Higgsfield 작업 id), `budget`(프로젝트 예산이 있으면 `{"limit": 700.0, "by": "owner", "approved": 80.0, "remaining": 620.0}`, 없으면 `null`). 승인한 순간의 예산을 넘긴 승인 줄은 `problems`에 나온다.
- `ok`가 `false`면 종료 코드 2. `problems`에 `ledger line 16 is invalid: ...`처럼 문제 줄이 적힌다. UTF-8로 읽을 수 없는 줄도 문제 줄 하나로 적히고 나머지 줄은 그대로 읽힌다.

### takes cuts

테이크에서 숏의 경계를 정할 때 볼 값을 낸다. 테이크를 `conform`과 똑같이 원본의 프레임 속도와 화면으로 바꾼 뒤 읽는다.

```powershell
& $cr takes cuts $p --unit u01 --take t001
```

- 출력: JSON. `frames`(프레임 속도를 바꾼 뒤의 테이크 프레임 수), `unit_frames`(단위의 프레임 수), `pad_frames`(계획에 적힌 붙인 프레임 수. 키가 없으면 0), `cuts`(분석과 같은 컷 검출로 테이크에서 찾은 컷 프레임), `tail_static_from`(끝까지 이어지는 멈춘 화면에서 앞 프레임의 사본인 첫 프레임, 곧 마지막 실제 프레임의 다음 프레임. 없으면 `null`).

  ```json
  {"frames": 97, "unit_frames": 30, "pad_frames": 66, "cuts": [], "tail_static_from": 33}
  ```
- 프레임 번호는 `conform`의 `--start-frame`, `--take-cuts`와 같이 프레임 속도를 바꾼 뒤의 테이크 프레임이다. 이 번호로 프레임을 펼쳐 보는 명령은 아래 "테이크의 프레임 구간"에 있다.
- `tail_static_from`: 마지막 프레임에서 거꾸로 가며, 다음 프레임과의 차이가 0.25 미만인 동안 같은 구간으로 본다. 차이는 컷 검출이 쓰는 64x36 회색 축소 화면의 평균 절대 차(0-255)다. 사본끼리는 0.05 안팎이고, 느리게 움직이는 실제 숏도 프레임마다 0.6에서 1.3쯤 달라지므로 구간에 들지 않는다. 구간이 6프레임 이상일 때만 나오고(마지막 실제 프레임 포함), 값은 구간의 첫 프레임(마지막 실제 프레임)의 다음 프레임이다. 그래서 `--take-cuts "0,<tail_static_from>"`은 마지막 실제 프레임까지 쓰고 사본을 모두 뺀다. 패딩한 단위에서 모델이 돌려준 멈춘 끝을 찾는 데 쓴다.
- 숏 하나인 단위의 테이크에 `cuts`가 있으면 모델이 원본에 없는 컷을 넣었거나 잘못된 컷을 남긴 것이고, 그 테이크는 쓸 수 없다.
- 테이크 폴더에는 아무것도 쓰지 않는다. 바꾼 테이크는 임시 폴더에 두었다가 지운다.
- 거부: 그런 테이크가 없다. 현재 계획에 없는 단위다.

### conform

테이크를 원본에 맞춘다. 프레임 속도를 원본으로 바꾸고, 화면 영역을 채우도록 크기를 맞춰 넘치는 부분을 잘라 내고, 원본과 같은 검은 띠를 붙인 뒤, 숏마다 원본과 같은 프레임 수로 자른다.

```powershell
& $cr conform $p --unit u01 --take t001 --take-cuts "0,33"
& $cr conform $p --unit u03 --take t001 --take-cuts "0,88,129"
& $cr conform $p --unit u01 --take t001
& $cr conform $p --unit u01 --take t001 --start-frame 3
& $cr conform $p --unit u01 --take t001 --allow-retime
```

- 쓰는 것: `takes/<단위>/<테이크>/shots/NNNN.mov`(ProRes), `conform.json`(`start_frame`, `retimed`, `source_frames`, `unit_frames`, 그리고 `--take-cuts`를 썼으면 `take_cuts`와 `segments`). 다시 실행하면 새로 만든다. `raw` 파일은 읽기만 한다. 다시 맞추면 그 테이크의 `review.mp4`, `review.jpg`를 지우고, 그 테이크가 단위의 고른 테이크이면 단위의 사용자 확정(`confirmed`)도 지운다. 화면이 바뀌었으니 `takes review`부터 다시 한다.
- 출력: 숏 클립의 경로를 한 줄에 하나씩.
- `--take-cuts`: 단위의 숏마다 테이크에서 그 숏이 시작하는 프레임을 차례로, 끝에 마지막 숏이 끝난 다음 프레임을 적는다. 숏 수보다 하나 많은 정수를 쉼표로 잇고 따옴표로 감싼다. 번호는 `--start-frame`처럼 프레임 속도를 바꾼 뒤의 테이크 프레임이다(`takes cuts`의 번호와 같다). 구간 `[b_i, b_i+1)`이 i번째 숏의 클립이 되고, 구간의 프레임 수가 원본 숏과 다르면 `--allow-retime`과 같은 방식(구간 안에서 고르게, 처음과 끝 프레임을 모두 쓰는 표본)으로 늘리거나 줄여 맞추며, 같으면 프레임 하나하나 그대로 옮긴다. 테이크가 단위와 맞지 않을 때(프레임 수가 다르거나 컷이 어긋났을 때) 쓴다. 패딩한 단위의 끝 경계는 `tail_static_from`(마지막 실제 프레임의 다음 프레임)이다.
- `--take-cuts`를 쓰면 `conform.json`의 `start_frame`은 첫 값, `take_cuts`는 준 값, `segments`는 숏마다 `{"shot": 2, "take_start": 88, "take_end": 129, "frames": 42, "retimed": true}`(숏 번호, 테이크 구간, 원본 숏의 프레임 수, 늘리거나 줄였는지)이고, `retimed`는 구간 하나라도 늘리거나 줄였으면 `true`다.
- `--start-frame`: 프레임 속도를 바꾼 뒤의 테이크에서 단위의 첫 프레임이 될 프레임 번호. 기본 0. `--take-cuts` 없이 쓴다.
- `--allow-retime`: 테이크가 단위보다 짧을 때 고르게 늘려 프레임 수를 맞춘다. 동작 속도가 달라진다. `--take-cuts` 없이 쓴다.
- `--take-cuts` 없이 실행하면 테이크가 `--start-frame`부터 단위와 프레임 하나하나 맞는다고 보고 자른다.
- 패딩한 단위의 붙인 프레임은 숏 클립에 들어가지 않는다. 숏 클립은 언제나 원본 숏의 프레임 수다.
- 거부: 테이크가 짧다(`take too short: have 20 frames, need 30`, `--take-cuts`를 쓰지 않을 때). 그런 테이크가 없다. 현재 계획에 없는 단위다. `--take-cuts`의 값 수가 숏 수보다 하나 많지 않다. 값이 0 이상에서 시작해 엄격히 오름차순인 정수가 아니다. 마지막 값이 테이크의 프레임 수보다 크다. 어떤 구간의 프레임 수가 원본 숏과 25퍼센트 넘게 다르다(숏 번호와 두 프레임 수가 나온다. 경계를 잘못 정했거나, 모델이 숏을 바꾸거나 빼먹어 테이크를 다시 생성해야 한다). `--take-cuts`를 `--start-frame`(0이 아닌 값)이나 `--allow-retime`과 함께 썼다.

### qc

맞춘 테이크를 원본과 비교해 숏마다 점수를 낸다.

```powershell
& $cr qc $p --unit u01 --take t001
```

- 쓰는 것: `takes/<단위>/<테이크>/scores.json`. 출력도 같은 JSON이다(아래 "점수 읽기").
- 기준은 `project.json`의 `thresholds`에서 읽는다. 판정에서 빼는 점수는 숏마다 정한다. `casting.json`에서 교체가 있는 `background` 요소가 나오는 숏은 `color`가 자동으로 빠지고, 단위의 `qc_skip`은 그 위에 더해져 단위의 모든 숏에 적용된다.
- 거부: `conform`을 하지 않았다. `qc_skip`에 모르는 이름이 있다.

### takes select

단위에서 완성본에 쓸 테이크를 고른다. 다시 실행하면 선택이 바뀐다.

```powershell
& $cr takes select $p --unit u01 --take t001
```

- 쓰는 것: `plan.json`의 `selected_take`. 출력: `u01: selected take t001`.
- 지금 고른 것과 다른 테이크를 고르면 그 단위의 사용자 확정(`confirmed`)이 지워진다. 같은 테이크를 다시 고르면 남는다.

### takes review

맞춘 테이크를 사용자가 원본과 견주어 볼 파일 두 개를 테이크 폴더에 만든다.

```powershell
& $cr takes review $p --unit u01 --take t001
```

- 쓰는 것: `takes/<단위>/<테이크>/review.mp4`(왼쪽이 원본, 오른쪽이 맞춘 테이크. 단위의 숏을 순서대로, 둘 다 화면 영역만, 원본과 같은 프레임 속도와 프레임 수, 소리 없음. 한쪽의 크기는 화면 영역의 크기이고, 높이가 540보다 크면 540으로 줄인다), `review.jpg`(숏마다 한 줄. 원본의 첫, 가운데, 끝 프레임과 테이크의 같은 세 프레임이 너비 320인 칸에 놓이고, 칸마다 숏 번호, `ref`나 `take`, 영화의 프레임 번호가 적힌다).
- 출력: 두 파일의 경로를 한 줄에 하나씩.
- 두 파일에는 원본 화면이 들어 있으므로 보기만 한다. `jobs done`, `assets done`, `sound done`, `assemble --audio`는 `takes/` 안의 파일을 받지 않는다. 테이크를 다시 맞추면 두 파일은 지워진다.
- 거부: 그런 테이크가 없다. 테이크를 맞추지 않았다(`conform.json`이나 숏 클립이 없다). 현재 계획에 없는 단위다.

### takes confirm

사용자가 단위의 고른 테이크를 확정했다고 기록한다. 사용자가 그 숏을 받아들인다고 답했을 때만 실행한다.

```powershell
& $cr takes confirm $p --unit u01 --take t001 --by owner --note "accepted after review"
```

- 쓰는 것: `plan.json`의 그 단위에 `"confirmed": {"take": "t001", "by": "owner", "at": "2026-11-18T09:30:00Z", "note": "accepted after review"}`(`at`은 기록한 UTC 시각).
- 출력: `u01: take t001 confirmed by owner`.
- 확정이 지워지는 때: `takes select`로 다른 테이크를 고른다. 고른 테이크를 `conform`으로 다시 맞춘다. `plan build --replace`로 계획을 새로 만든다(새 단위에는 확정이 없다). `plan unit`은 지우지 않는다.
- 거부: 그 테이크가 단위의 고른 테이크가 아니다. 테이크를 맞추지 않았다. `--by`가 비어 있다. 현재 계획에 없는 단위다.

### assemble

단위마다 고른 테이크의 숏 클립을 원본 순서대로 이어 붙이고, 소리가 있으면 넣는다.

```powershell
& $cr assemble $p
& $cr assemble $p --preview
& $cr assemble $p --silent
& $cr assemble $p --audio "$p\audio\new_score.wav"
```

- 쓰는 것: `out/master.mov`(ProRes, 소리가 있으면 24비트 PCM), `out/final.mp4`(H.264, 소리가 있으면 AAC 256 kb/s). 출력: `final.mp4`의 경로, 그리고 들어간 소리를 알리는 줄 하나(`sound: soundtrack (3 items)`, `sound: file new_score.wav`, `sound: none`).
- 소리: `--silent`면 넣지 않는다. `--audio`를 주면 그 파일을 넣는다. 둘 다 없고 `out/soundtrack.wav`가 있으면 그것을 넣는다. 그 파일의 SHA-256이 `out/soundtrack.json`에 적힌 값과 다르면(믹스 뒤에 파일이 바뀌었으면) 거부한다. 사운드트랙이 없으면 소리 없는 영상이 된다. 넣은 소리는 영화 길이에 맞춰 자르거나 무음으로 채운다. 화면과 프레임 수 검사는 소리와 관계없이 같다.
- `--preview`: 확정이 없어도 고른 테이크로 `out/preview.mp4`만 만든다(`master.mov`와 `final.mp4`는 만들지 않고, 프레임 수 검사는 한다). 사운드트랙은 있고 SHA-256이 맞을 때만 넣고, 맞지 않으면 빼고 만든다. `--audio`와 함께 쓰지 않는다.
- `--silent`와 `--audio`는 함께 쓰지 않는다.
- 거부: 고른 테이크가 없는 단위가 있다. 사용자가 확정하지 않은 단위가 있다(`--preview` 제외. 메시지가 그 단위들과 `takes review`, `takes confirm`을 알려 준다). 고른 테이크를 `conform`하지 않았다. 이어 붙인 프레임 수가 원본과 다르다. `out/soundtrack.wav`가 `sound mix`가 쓴 파일과 다르다(`run sound mix again`, `--preview` 제외). `--audio`가 프로젝트의 `reference/`, `analysis/`, `takes/`, `out/` 안에 있는 파일이거나(`out/compare.mp4`에는 원본의 소리가 들어 있고, 편집 모델의 테이크에도 남아 있을 수 있다), 바이트가 참조 영상과 같은 파일이다(`init`에 준 원본 파일도 여기서 걸린다). 완성본의 오디오는 완성본을 위해 만든 파일이어야 한다.

### compare

원본과 완성본을 좌우로 나란히 놓은 영상을 만든다.

```powershell
& $cr compare $p
```

- 쓰는 것: `out/compare.mp4`. 왼쪽이 원본, 오른쪽이 완성본이고 원본에 소리가 있으면 그 소리가 들어간다. 검토용이다.
- 거부: `out/final.mp4`가 없다.

### sound add

소리 생성 작업을 하나 계획하고 영화의 프레임에 놓는다. 소리 하나는 프롬프트 하나와 길이 하나로 부르는 생성 호출 하나다.

```powershell
& $cr sound add $p --kind sfx --label "door slam" --prompt "Heavy wooden door slams shut in a stone hallway, short echo." --model audio_model_x --seconds 3 --batch sound-1 --at-frame 54
& $cr sound add $p --kind ambience --label "stone hall" --prompt "Quiet stone hall ambience, distant wind, faint dripping water." --model audio_model_x --seconds 12 --batch sound-1 --shot 2 --offset-frames -6 --gain-db -12
& $cr sound add $p --kind music --label "main theme" --prompt "Slow orchestral underscore, low strings and soft French horn, rising tension." --model audio_model_x --seconds 30 --batch sound-1 --gain-db -8 --fade-in 2 --fade-out 3
```

- `--kind`: `music`(배경음악), `sfx`(효과음), `ambience`(배경 소리).
- `--label`: 소리의 짧은 이름. 한국어도 되고 한자는 안 된다. `--prompt`: 영어. 소리 자체(재질, 동작, 공간, 세기)를 묘사하고, 작품, 작곡가, 연주자 이름이나 "누구 풍"은 쓰지 않는다.
- `--model`: `models.json`의 소리 모델 id. `--seconds`: 모델에 요청할 길이. 모델의 `min_seconds`에서 `max_seconds` 사이여야 한다.
- 시작: `--at-frame N`(영화의 프레임 N, 0부터) 또는 `--shot K`(숏 K의 첫 프레임)에 `--offset-frames F`(그 프레임에서 F만큼 옮긴다. 앞으로 당기면 음수)를 더한 프레임. 둘 다 없으면 0번 프레임이다. 시작 프레임은 0 이상이고 영화의 프레임 수보다 작아야 한다.
- `--gain-db`: 믹스할 때의 음량, -60에서 12(기본 0). `--fade-in`, `--fade-out`: 페이드 길이(초, 기본 0과 0.2).
- 쓰는 것: `sounds.json`. 출력: 소리 id(`s0001`). 한 번 쓴 id는 다시 쓰이지 않는다(장부에 있는 id도 센다).
- 거부: 모르는 종류다. 프롬프트가 영어가 아니다. 이름이나 프롬프트에 한자가 있다. 모델이 `models.json`에 없거나 소리 모델이 아니다. 길이가 모델 범위 밖이다. 시작 프레임이 영화 밖이다. 그런 숏이 없다. `--offset-frames`를 `--shot` 없이 썼다. `--at-frame`과 `--shot`을 함께 썼다. 음량이나 페이드가 범위 밖이다. 거부되면 아무것도 쓰지 않는다.

### sound list

소리 작업과, 작업마다 실제로 보낼 `params`와 놓는 자리를 보여 준다. 가격을 물을 때 이 `params`를 쓴다.

```powershell
& $cr sound list $p
```

- 출력: JSON 목록. 항목마다 `id`, `kind`, `label`, `batch`, `state`(`planned`, `quoted`, `issued`, `done`, `failed`), `est_credits`, `params`(`model`, `prompt`, `duration`. `duration`은 `--seconds`), `at_frame`, `gain_db`, `fade_in`, `fade_out`, `muted`, `file`(등록된 파일. 없으면 `null`).
- 내보낸 뒤의 작업은 내보낼 때 실제로 보낸 `params`가 나온다.

### sound set-cost

소리 작업의 견적을 기록한다. 견적은 모델과 길이에 묶인다(프롬프트와 놓는 자리는 묶이지 않는다).

```powershell
& $cr sound set-cost $p --sound s0001 --credits 4
```

- 출력: `s0001: quoted 4.0 credits`. 상태가 `quoted`가 된다. 내보내기 전에는 다시 실행해 견적을 바꿀 수 있다.
- 거부: 이미 내보낸 작업이다. 크레딧이 0 이하다. `sound add`의 검사를 통과하지 못한다.

### sound next

묶음에서 견적이 있는 소리 작업을 id 순서로 장부에 올리고, 실행할 내용을 낸다. 같은 묶음의 영상 작업, 자료 작업과 한도를 함께 쓴다.

```powershell
& $cr sound next $p --batch sound-1
```

- 출력: JSON 목록. 항목마다 `{"job_id": "s0001", "kind": "sfx", "tool": "generate_audio", "params": {"model": "audio_model_x", "prompt": "...", "duration": 3}, "medias": []}`. 내보낼 것이 없으면 `[]`.
- 한 번 내보낸 작업은 다시 나오지 않는다.
- 거부(종료 코드 2): 묶음에 승인 기록이 없다. 견적을 더하면 한도를 넘는다. 견적 뒤에 모델이나 길이가 바뀌었다. `sound add`의 검사를 통과하지 못한다. 장부가 거부하기 전에 내보낸 작업이 있으면 그 목록은 그대로 출력된다.

### sound done

돌려받은 소리 파일을 등록하고 작업을 끝난 것으로 기록한다.

```powershell
& $cr sound done $p --sound s0001 --provider-id 4d2c8a10-6e3b-4f1a-9c7d-2b5e8f0a1c3d --credits 4 --file "$p\downloads\s0001.wav"
```

- `--file`: `.wav`, `.mp3`, `.m4a`, `.ogg`, `.flac` 파일. `--provider-id`: Higgsfield의 작업 id. `--credits`: 실제로 차감된 크레딧.
- 쓰는 것: `sounds/<소리 id><확장자>`(`sounds/s0001.wav`), `sounds.json`, `ledger.jsonl`. 장부에 `done`으로 기록된 소리의 파일은 바꾸지 않는다. 장부에 `done`이 없는데 그 id로 된 파일이 이미 있으면 앞서 끊긴 `sound done`이 남긴 것이므로 지우고 이번 파일을 저장한다.
- 출력: `s0001: done, file sounds/s0001.wav`.
- 거부: 내보낸 상태(`issued`)가 아니다. 파일이 없거나 확장자가 위의 것이 아니다. ffprobe가 읽을 수 있는 소리 스트림이 없다. 프로젝트의 `reference/`, `analysis/`, `takes/`, `out/`, `assets/`, `sounds/` 안에 있는 파일이다. 바이트가 참조 영상과 같은 파일이다. 생성 작업에서 내려받은 파일만 등록한다.

### sound fail

실패한 소리 작업을 기록한다.

```powershell
& $cr sound fail $p --sound s0002 --reason "provider error: timeout" --credits 0
```

- `--credits`: 실패했는데도 차감된 크레딧. 기본 0. 출력: `s0002: failed`. 다시 하려면 `sound add`로 새 작업을 만든다.

### sound set

소리의 시작 프레임, 음량, 페이드, 끄기를 바꾼다. 견적에 묶이지 않으므로 어느 상태에서든 언제나 바꿀 수 있고 비용이 들지 않는다. 주지 않은 값은 그대로 남는다.

```powershell
& $cr sound set $p --sound s0002 --at-frame 56 --gain-db -3
& $cr sound set $p --sound s0003 --shot 3 --offset-frames 2 --fade-out 0.5
& $cr sound set $p --sound s0001 --mute
& $cr sound set $p --sound s0001 --unmute
```

- 출력: 소리의 자리 한 줄. `s0002: frame 56 (2.333 s), gain -3.0 dB, fade in 0.0 s, fade out 0.2 s`. 꺼진 소리는 끝에 `, muted`가 붙는다.
- `--mute`: 믹스에서 뺀다. `--unmute`: 다시 넣는다. 둘을 함께 쓰지 않는다.
- 바꾼 뒤에는 `sound mix`를 다시 해야 사운드트랙에 반영된다. `report`는 믹스 뒤에 바뀐 배치를 열린 항목에 적는다.
- 거부: `sound add`와 같은 시작, 음량, 페이드 규칙에 맞지 않는다.

### sound mix

끝났고 끄지 않은 소리를 모두 제자리에 놓고 더해 사운드트랙을 만든다.

```powershell
& $cr sound mix $p
```

- 쓰는 것: `out/soundtrack.wav`(24비트 PCM, 48 kHz, 스테레오, 영화 길이와 샘플 단위까지 같다)와 `out/soundtrack.json`(아래 "파일 형식").
- 방법: 소리마다 파일을 48 kHz 스테레오로 읽고, `seconds`보다 길면 자르고, 페이드를 걸고, `gain_db`만큼 키우거나 줄여 `at_frame`의 시각에 놓는다. 영화 끝을 넘는 부분은 50 ms 페이드로 자른다. 소리들은 서로 나누지 않고 그대로 더한다. 더한 소리의 통합 음량을 재어 고정 이득 하나로 -16 LUFS에 맞추고, 리미터로 최고 레벨을 -1 dBFS 아래로 누른다.
- 원본 검사: 참조 영상에 소리가 있으면 믹스와 원본 소리를 모두 8 kHz 모노로 바꿔 앞뒤 0.5초 안의 정규화 상호상관 최댓값을 잰다. 0.5 이상이면 믹스에 원본의 소리가 들어 있는 것이므로 거부하고 아무 파일도 남기지 않는다. 그보다 작으면 값을 `reference_correlation`에 적는다. 원본에 소리가 없으면 `null`이다.
- 출력: 첫 줄에 `soundtrack.wav`의 경로, 둘째 줄에 `loudness -16.0 LUFS  peak -3.2 dBFS  reference correlation 0.0312`(원본에 소리가 없으면 `reference correlation none (the reference has no sound)`).
- 거부: 끝났고 끄지 않은 소리가 없다. 더한 소리가 완전한 무음이거나 음량을 잴 수 없을 만큼 작다. 끝난 소리의 파일이 없다. `sounds.json`의 자리 값이 규칙에 맞지 않는다(손으로 고친 경우). 믹스가 원본의 소리와 닮았다. 거부되면 사운드트랙을 쓰지 않는다.

### report

한국어 보고서를 쓴다.

```powershell
& $cr report $p
```

- 숏별 결과의 모델은 고른 테이크를 실제로 만든 작업이 보낸 모델이다(`plan unit`으로 단위의 모델을 바꾼 뒤에도 그 테이크의 모델이 나온다). 고른 테이크가 없거나 작업을 알 수 없으면 단위의 지금 모델이다.
- 쓰는 것: `out/report.md`. 요약, 권리 확인, 교체표, 숏별 결과, 단위별 사용자 확인, 소리, 비용, 열린 항목(고른 테이크가 없는 단위, 사용자가 확인하지 않은 단위, 판정이 `retry`인 숏, 고른 테이크에서 `overlap_ratio`가 0.98 이상이라 화면이 원본과 거의 같은 숏, 내보냈지만 기록을 닫지 않은 소리, 사운드트랙 뒤에 바뀐 소리 배치). 원본과 거의 같은 숏의 항목은 판정을 바꾸지 않는다. 원본을 복사한 화면이 아닌지 사용자가 볼 목록이다.
- 권리 확인: `yes`가 아닌 답에는 "확인 필요"가 붙는다. 다만 `likeness_consent`의 `not_applicable`은 도구가 확인할 수 있으면(교체하는 인물이 없거나, 인물 교체 이미지가 모두 참조 이미지 없이 생성한 파일) 붙지 않고 도구가 확인했다고 적힌다.
- 단위별 사용자 확인: 단위마다 숏, 고른 테이크, 확정한 사용자와 시각(메모가 있으면 메모). 확정이 없거나 고른 테이크와 다른 테이크의 확정이면 "확인 안 함"이다.
- 소리: 소리마다 id, 종류, 이름, 시작 프레임, 길이, 음량, 상태(꺼진 소리는 "음소거"). 사운드트랙이 있으면 음량, 최고 레벨, 원본 소리와의 상관, 컷마다 시작하는 소리. 사운드트랙의 배치가 지금의 `sounds.json`과 다르거나(믹스 뒤에 `sound set`을 했거나 소리가 더 끝났다) 파일이 믹스 기록과 다르면 열린 항목에 "sound mix를 다시 한다"가 적힌다.
- 비용: 묶음마다 한도, 사용, 잔액과 모든 묶음의 합계 줄, 프로젝트 예산과 승인한 묶음 한도의 합과 남은 예산(예산이 없으면 "정하지 않음"), 그 아래 끝난 작업의 Higgsfield 작업 id 목록.
- 계획의 일부만 끝난 상태에서도 쓸 수 있다.

## 파일 형식

`casting.json`과 `plan.json`은 고칠 키를 그 자리에서 고친다. 고친 뒤에도 올바른 UTF-8 JSON이기만 하면 어떤 도구로 고쳐도 되고, 줄 배치와 들여쓰기는 상관없다. 검사 명령이 파일을 다시 읽는다.

### casting.json

에이전트가 쓴다. `casting.template.json`을 복사해 채워도 된다.

```json
{
  "elements": [
    {"id": "hero", "kind": "person", "name": "Rider", "shots": [1, 3],
     "look": "teenage boy, tousled brown hair, green wool tunic, dark leather vest"},
    {"id": "sky_battle", "kind": "background", "name": "Sky around the giant", "shots": [1, 3],
     "look": "grey-green smoke, falling rock debris, overcast light"},
    {"id": "dragon", "kind": "creature", "name": "Black dragon", "shots": [1, 2, 3],
     "look": "small black dragon with green eyes"}
  ],
  "swaps": [
    {"element": "hero", "images": ["swap/person_front.jpg"], "note": "photo supplied by the user"},
    {"element": "sky_battle", "images": ["swap/sunset_sky.png"], "note": "image supplied by the user"}
  ]
}
```

| 키 | 규칙 |
| --- | --- |
| `elements[].id` | 영문 소문자로 시작하고 소문자, 숫자, 밑줄로 된 2-24자. 겹치면 안 된다 |
| `elements[].kind` | `person`, `creature`, `prop`, `background` |
| `elements[].name` | 보고서에 나오는 이름. 문자열이고 한글도 된다 |
| `elements[].shots` | 그 요소가 보이는 숏 번호(정수). 비어 있으면 안 된다 |
| `elements[].look` | 원본에서 보이는 모습. 영어 |
| `swaps[].element` | 교체할 요소의 id. 요소 하나에 항목 하나 |
| `swaps[].images` | 교체 이미지. 한 장 이상이고, 상대 경로는 프로젝트 폴더 기준이다. 여러 장이면 적힌 순서대로 모두 입력이 된다 |
| `swaps[].note` | 메모. 생략해도 된다 |

교체표에 없는 요소(위의 `dragon`)는 원본과 같게 재현한다. 어떤 단위의 숏에 나오는 요소의 교체 이미지는 모두 그 단위의 입력으로 들어가며, 순서는 요소 목록의 순서다.

### plan.json에서 에이전트가 고치는 곳

`plan.json`은 `plan build`가 만들고, 에이전트는 단위의 다음 세 곳만 고친다.

| 키 | 고치는 때 |
| --- | --- |
| `units[].prompt` | 언제나. 영어 프롬프트를 쓴다. 견적에 묶이지 않으므로 견적 뒤에 고쳐도 된다. 이미 내보낸 작업에는 내보낼 때의 프롬프트가 쓰인다 |
| `units[].qc_skip` | 교체 때문에 당연히 달라지는 점수를 판정에서 뺄 때. `"camera"`, `"motion"`, `"color"`, `"flicker_ratio"` 가운데 적고, 단위의 모든 숏에 적용된다. 배경을 교체한 숏의 `color`는 도구가 자동으로 빼므로 적지 않아도 된다. 그 밖에 이유를 댈 수 있는 경우에 쓰고, 넣기 전에 사용자에게 알린다 |
| `units[].params.duration` | 모델이 정해진 길이만 받을 때. 단위 길이보다 짧지 않은 허용 값으로 고친다. 견적 전에 고치고, 견적 뒤에 고쳤으면 다시 견적을 기록한다 |

작업 목록, `state`, `est_credits`, `inputs`, `selected_take`, `pad_frames`, `model`, `method`, `confirmed`는 손으로 고치지 않는다. `confirmed`는 `takes confirm`만 쓴다. 입력을 바꾸려면 `casting.json`을 고치고 바뀐 단위마다 `plan unit`을 한다. 단위의 모델이나 방법은 `plan unit --model`, `--method`로 바꾼다. `inputs`가 지금의 교체표와 다르면 `plan check`와 `jobs next`가 거부한다.

### 점수 읽기

`qc`의 출력과 `scores.json`은 같은 내용이다. 맨 위 `verdict`는 모든 숏이 `pass`일 때만 `pass`다. `shots`의 숏마다 다음이 있다.

| 키 | 뜻 | 기준(`project.json`의 `thresholds`) |
| --- | --- | --- |
| `frames_ref`, `frames_take`, `timing_ok` | 원본과 테이크의 프레임 수, 같은지 | 같아야 한다 |
| `camera` | 카메라 움직임이 닮은 정도. 0에서 1 | `camera_min` 0.6 이상 |
| `motion` | 화면 속 움직임이 닮은 정도. 0에서 1 | `motion_min` 0.5 이상 |
| `color` | 프레임의 평균 색이 가까운 정도. 0에서 1 | `color_min` 0.6 이상 |
| `flicker_ratio` | 테이크의 깜빡임을 원본의 깜빡임으로 나눈 값 | `flicker_ratio_max` 2.0 이하 |
| `overlap_ratio` | 원본과 거의 같은 화면 영역의 비율. 0에서 1 | 판정에 쓰지 않는다 |
| `verdict`, `failed` | 그 숏의 판정과 기준에 못 미친 항목. `failed`에는 `skipped`의 이름이 들어가지 않는다 | |
| `skipped` | 판정에서 뺀 점수 이름의 정렬된 목록. 배경을 교체한 숏의 `"color"`(자동)와 단위의 `qc_skip`(손으로)을 합친 것이다. 점수 자체는 그대로 계산해 적는다 | |

보고서의 숏별 표에서는 판정에서 뺀 점수 뒤에 `(제외)`가 붙는다.

### sounds.json

`sound` 명령들이 쓴다. 손으로 고치지 않는다. 놓는 자리는 `sound set`으로 바꾼다.

```json
{
  "sounds": [
    {"id": "s0001", "kind": "sfx", "label": "door slam",
     "prompt": "Heavy wooden door slams shut in a stone hallway, short echo.", "model": "audio_model_x",
     "seconds": 3, "batch": "sound-1", "state": "done", "est_credits": 4.0, "quote_digest": "...",
     "sent_params": {"model": "audio_model_x", "prompt": "Heavy wooden door slams shut in a stone hallway, short echo.", "duration": 3},
     "file": "sounds/s0001.wav", "at_frame": 54, "gain_db": 0.0, "fade_in": 0.0, "fade_out": 0.2, "muted": false}
  ]
}
```

| 키 | 뜻 |
| --- | --- |
| `id` | `s`와 숫자. 영상 작업 id, 자료 id와 같은 장부를 쓴다 |
| `kind`, `label`, `prompt`, `model`, `seconds`, `batch` | `sound add`에 준 값. `seconds`가 생성 호출의 `duration`으로 나간다 |
| `state`, `est_credits`, `quote_digest`, `sent_params` | 생명주기. 상태는 장부가 정하고, 견적은 모델과 길이에 묶인다 |
| `file` | `sound done`이 저장한 파일. 끝나기 전에는 `null` |
| `at_frame` | 소리가 시작하는 영화의 프레임(0 이상, 프레임 수 미만) |
| `gain_db` | 믹스 음량, -60에서 12 |
| `fade_in`, `fade_out` | 페이드 길이(초, 0 이상) |
| `muted` | `true`면 믹스에서 뺀다 |

### analysis/audio.json

`analyze`가 쓴다. 원본 소리의 타이밍이고 소리 자체가 아니다. 이 파일로 원본의 소리를 되살릴 수 없다.

```json
{
  "has_audio": true,
  "frames": 72,
  "rms_db": [-90.0, -90.0, -38.21, -61.5],
  "hits": [55, 10, 30],
  "shots": [
    {"shot": 1, "start": 0, "end": 30, "mean_db": -42.17, "hits": [10]},
    {"shot": 2, "start": 30, "end": 72, "mean_db": -39.8, "hits": [30, 55]}
  ]
}
```

| 키 | 뜻 |
| --- | --- |
| `has_audio` | 참조 영상에 소리 스트림이 있는가. 없으면 `false`이고 목록은 모두 비어 있다 |
| `frames` | 영화의 프레임 수 |
| `rms_db` | 영화의 프레임마다 그 프레임 동안의 소리(모노)의 RMS 음량(dBFS). 가장 낮은 값은 -90이다 |
| `hits` | 소리가 크게 터지는 프레임. 음량이 앞 6프레임 평균보다 9 dB 이상 뛴 프레임 가운데 서로 6프레임 이상 떨어진 것을, 크게 뛴 것부터 40개까지 |
| `shots` | 숏마다 첫 프레임, 끝 다음 프레임, 숏 전체 소리의 RMS 음량(`mean_db`), 숏 안의 `hits`(오름차순) |

### out/soundtrack.json

`sound mix`가 쓴다. `assemble`은 `soundtrack.wav`의 SHA-256이 이 파일의 `sha256`과 같을 때만 사운드트랙을 넣는다.

```json
{
  "sounds": [
    {"id": "s0001", "kind": "sfx", "label": "door slam", "start_seconds": 2.25, "at_frame": 54, "seconds": 3,
     "gain_db": 0.0, "fade_in": 0.0, "fade_out": 0.2}
  ],
  "loudness_before": -23.4,
  "gain_applied_db": 7.4,
  "loudness_after": -16.02,
  "peak_dbfs": -3.18,
  "duration_seconds": 3.75,
  "sha256": "9f2c...",
  "reference_correlation": 0.0312,
  "cuts": [{"frame": 30, "sounds": []}, {"frame": 54, "sounds": ["s0001"]}]
}
```

| 키 | 뜻 |
| --- | --- |
| `sounds` | 믹스에 놓인 소리(끝났고 끄지 않은 것)와 그 자리. `start_seconds`는 `at_frame`을 초로 바꾼 값이다 |
| `loudness_before`, `gain_applied_db`, `loudness_after` | 더한 소리의 통합 음량(LUFS), 걸어 준 고정 이득(dB), 리미터를 거친 결과의 통합 음량 |
| `peak_dbfs` | 결과의 최고 샘플 레벨(dBFS). -1 이하다 |
| `duration_seconds` | 사운드트랙의 길이. 영화의 길이와 같다 |
| `sha256` | `soundtrack.wav`의 SHA-256 |
| `reference_correlation` | 원본 소리와의 상관(0.5 미만). 원본에 소리가 없으면 `null` |
| `cuts` | 영화의 컷마다 그 컷의 앞뒤 2프레임 안에서 시작하는 소리의 id |

## 검수용 스틸

영상은 스틸 띠로 만들어서 본다. 결과 이미지는 프로젝트의 `review/` 폴더에 둔다. 폴더는 먼저 만든다.

```powershell
New-Item -ItemType Directory -Force "$p\review" | Out-Null
```

**맞추기 전의 테이크.** 첫 프레임부터 12프레임마다 한 장씩, 열여섯 칸까지 뽑는다. 첫 칸이 테이크의 첫 프레임이다. 올바른 숏인지, 첫 프레임이 원본의 첫 프레임과 같은 순간인지 본다. 테이크가 더 길면 간격 `12`를 늘린다. 테이크가 짧아 뽑히는 장수(프레임 수를 12로 나눠 올림)가 칸 수보다 적으면, `tile`의 줄 수를 그 장수를 열 수(8)로 나눠 올림한 값으로 줄인다(90프레임이면 8장이므로 `tile=8x1`). 그래야 이미지 아래쪽이 검게 비지 않는다.

```powershell
ffmpeg -hide_banner -loglevel error -y -i "$p\takes\u01\t001\raw.mp4" -vf "select='not(mod(n\,12))',scale=320:-2,tile=8x2" -frames:v 1 -update 1 "$p\review\u01_t001_raw.png"
```

**테이크의 프레임 구간.** `takes cuts`가 보고한 번호 둘레를 펼쳐 숏의 경계를 정할 때 쓴다. 앞의 `fps=24000/1001`은 원본의 프레임 속도(`analysis/video.json`의 `fps_num`/`fps_den`)로 바꾼다. 먼저 이것으로 바꿔야 칸의 프레임 번호가 `takes cuts`와 `--take-cuts`의 번호와 같다. 프레임 84부터 95까지 열두 장이 왼쪽 위부터 차례로 놓인다. 컷 둘레면 장면이 바뀌는 칸의 번호가 다음 숏이 시작하는 경계다. 패딩한 단위의 테이크는 끝에 멈춘 화면이 이어지므로 `tail_static_from` 둘레를 펼쳐, 마지막 실제 프레임이 `tail_static_from`의 바로 앞 프레임인지 확인한다. 끝 경계는 마지막 실제 프레임의 다음 프레임(보통 `tail_static_from`)이다.

```powershell
ffmpeg -hide_banner -loglevel error -y -i "$p\takes\u03\t001\raw.mp4" -vf "fps=24000/1001,select='between(n\,84\,95)',scale=320:-2,tile=6x2" -frames:v 1 -update 1 "$p\review\u03_t001_0084-0095.png"
```

**맞춘 숏 클립.** 첫 프레임, 끝 프레임, 그 사이를 고르게 뽑는다. `5`는 간격으로 숏의 프레임 수를 8로 나눠 올림한 값이고, `35`는 끝 프레임 번호로 프레임 수에서 1을 뺀 값이다. 숏의 프레임 수는 `casting.template.json`의 `start`와 `end`의 차다. 원본의 `analysis/review/shot_NNNN.png`, 스틸 `first.png`와 `last.png`와 나란히 놓고 본다.

```powershell
ffmpeg -hide_banner -loglevel error -y -i "$p\takes\u01\t001\shots\0003.mov" -vf "select='not(mod(n\,5))+eq(n\,35)',scale=320:-2,tile=9x1" -frames:v 1 -update 1 "$p\review\u01_t001_0003.png"
```

**비교 영상.** 칸마다 왼쪽이 원본, 오른쪽이 결과다. 첫 프레임부터 12프레임마다 한 장씩, 열여섯 칸까지 뽑는다. 영상이 더 길면 간격 `12`를 늘리거나 `tile`의 줄 수를 늘린다. 짧으면 `tile`의 줄 수를 뽑히는 장수(프레임 수를 12로 나눠 올림)를 열 수(2)로 나눠 올림한 값으로 줄인다(90프레임이면 8장이므로 `tile=2x4`).

```powershell
ffmpeg -hide_banner -loglevel error -y -i "$p\out\compare.mp4" -vf "select='not(mod(n\,12))',scale=960:-2,tile=2x8" -frames:v 1 -update 1 "$p\review\compare.png"
```

**원본 한 프레임, 원래 크기.** 화면 영역을 잴 때 쓴다. 크기를 줄이지 않으므로 이미지의 픽셀 좌표가 곧 `--picture`의 값이다. 화면이 밝은 프레임을 골라 `40`을 바꾼다. 화면의 왼쪽 위 모서리가 x, y이고, 오른쪽 아래 모서리까지가 너비와 높이다. 홀수가 나오면 영역이 화면 안쪽으로 줄어드는 쪽으로 짝수에 맞춘다(x와 y는 올리고, 너비와 높이는 내린다).

```powershell
ffmpeg -hide_banner -loglevel error -y -i "$p\reference\source.mp4" -vf "select='eq(n\,40)'" -frames:v 1 -update 1 "$p\review\ref_frame_0040.png"
```

**원본의 프레임 구간.** 놓친 컷의 정확한 프레임을 찾을 때 쓴다. 프레임 24부터 35까지 열두 장이 왼쪽 위부터 차례로 놓인다. 장면이 바뀌는 칸의 프레임 번호가 컷이다. 참조 영상의 파일 이름은 `project.json`의 `reference`에 있다.

```powershell
ffmpeg -hide_banner -loglevel error -y -i "$p\reference\source.mp4" -vf "select='between(n\,24\,35)',scale=320:-2,tile=6x2" -frames:v 1 -update 1 "$p\review\ref_0024-0035.png"
```

## 후보 모델 비교

어떤 모델과 방법이 이 영상에 맞는지 모를 때, 숏 한두 개를 후보마다 만들어 비교한다. 이 프로젝트에서 처음 다루는 종류의 영상이면 나머지를 계획하기 전에 먼저 한다. 시험할 숏은 교체할 인물이 화면에서 가장 크게 나오는 한두 개로 고른다. 얼굴과 모습이 지켜졌는지가 가장 잘 드러나는 숏이다. 한 프로젝트 안에서 후보를 차례로 바꿔 가며 한다.

1. `models.json`에 후보 모델을 모두 적는다.
2. 첫 후보로 `--per-shot` 계획을 만든다. 숏마다 단위가 하나씩 생기므로 시험할 숏의 단위만 고를 수 있다.

   ```powershell
   & $cr plan build $p --model seedance_2_5 --method edit --batch cmp-edit --per-shot
   ```
3. 시험할 단위에만 프롬프트를 쓰고, 그 단위의 작업에만 `jobs set-cost`를 한다. 견적이 없는 작업은 나가지 않으므로 나머지 단위에는 비용이 들지 않는다. `jobs next`는 내보낼 작업의 단위만 검사하므로 나머지 단위의 프롬프트는 비워 둬도 된다(`plan check`는 그 단위들을 문제로 보여 준다).
4. 비용 표를 보여 주고 승인받은 뒤 `jobs next`, 생성, `jobs done`, `takes cuts`, `conform`, `qc`, 스틸 검수까지 마친다. 점수는 `takes/<단위>/<테이크>/scores.json`에 남는다.
5. 다음 후보로 계획을 바꾼다. 묶음 이름은 후보마다 다르게 한다.

   ```powershell
   & $cr plan build $p --model seedance_2_5 --method regen --batch cmp-regen --per-shot --replace
   ```

   이전 계획은 `plans/plan-0001.json`으로 보관되고, 새 단위와 작업은 이어지는 번호(숏이 셋이면 `u04`, `j0004`부터)를 받는다. 이전 단위의 테이크와 점수 파일은 `takes/`에 그대로 남는다. 다만 이전 단위는 현재 계획에 없으므로 `takes cuts`, `conform`, `qc`를 더 할 수 없다. 계획을 바꾸기 전에 끝낸다.
6. 내보냈지만 끝나지 않은 작업이 있으면 `--replace`가 거부된다. `jobs done`이나 `jobs fail`로 닫고 다시 한다.
7. 후보마다 네 가지를 비교한다. 교체 이미지와 같은 사람(크리처면 같은 크리처)으로 보이는가, 모든 숏에서 같은 디자인인가, 숏과 카메라가 원본 그대로인가(숏이 다른 화면으로 바뀌거나 빠지거나 늘어나지 않았는가), 타이밍이 맞는가. 후보, 숏, 점수, 눈으로 본 평가, 쓴 크레딧을 표로 사용자에게 보여 주고 기본 방법과 모델을 정한다. 어느 단위가 어느 모델이었는지는 보관된 계획 파일의 `units[].model`과 `units[].method`에 있다.
8. 정한 방법과 모델로 `plan build --replace`(`edit`면 `--per-shot`과 함께)를 해서 전체 계획을 만들고 SKILL.md의 4단계부터 이어 간다. 숏 여럿을 한 단위로 묶으려면, 고른 모델이 단위 안의 숏을 따로 지키는지를 숏 여럿인 단위 하나로 먼저 시험한다.
