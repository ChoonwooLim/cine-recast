# 명령별 참고

`cine-recast`의 모든 명령을 작업 순서대로 적었다. 명령마다 읽는 파일, 쓰는 파일, 출력, 거부되는 경우가 있다. 예시는 SKILL.md와 같이 두 변수를 쓴다.

```powershell
$cr = "$HOME/.cine-recast/bin/cine-recast.ps1"
$p = "$env:USERPROFILE\Videos\cine-recast\my-project"
```

- 첫 위치 인자는 항상 프로젝트 폴더다.
- 성공하면 종료 코드 0, 거부되거나 입력이 틀리면 2이고 이유가 `error:` 줄로 나온다. 1은 도구의 결함이다(`error: unexpected failure (...); this is a defect in cine-recast` 한 줄). 환경 변수 `CINE_RECAST_DEBUG=1`을 두면 그 뒤에 추적 정보가 붙는다.
- 프로젝트의 JSON 파일(`project.json`, `analysis/video.json`, `analysis/cuts.json`, `casting.json`, `models.json`, `plan.json`, `assets.json`)은 UTF-8로 읽고, 맨 앞의 BOM은 받아들인다. UTF-8이 아니거나 JSON이 깨졌으면 `error: casting.json is not valid JSON: ... at line 3, column 5`처럼 파일 이름(프로젝트 폴더 기준)과 줄과 칸이 나오고, 꼭 있어야 할 키가 없거나 맨 위가 객체가 아니면 파일 이름과 키가 나온다.
- `plan show`, `jobs list`, `jobs next`, `assets list`, `assets next`, `qc`, `audit`은 표준 출력에 JSON을 쓴다.
- 예시의 id, 숫자, 경로는 예시다.

## 프로젝트 폴더 구조

오른쪽은 그 파일을 쓰는 명령이다. 어떤 파일이 있는지 보면 프로젝트가 어느 단계까지 왔는지 알 수 있다.

```text
<프로젝트>/
  project.json                      init, rights set
  reference/source.mp4              init (참조 영상의 사본)
  analysis/
    video.json, cuts.json           analyze
    shots/0001/ref.mp4              analyze (화면 영역만 잘라 낸 숏 클립)
    shots/0001/sheet.json           analyze (숏 시트)
    shots/0001/stills/first.png, mid.png, last.png
    review/cut_0030.png, shot_0001.png      cuts review
    units/u01/ref.mp4               plan build (단위의 참조 클립)
  casting.template.json             cast template
  casting.json                      에이전트가 쓴다. assets use가 swaps를 고친다
  models.json                       에이전트가 쓴다
  assets.json                       assets add, set-cost, next, done, fail
  assets/hero/a0001_1.png           assets done (생성된 교체 자료)
  plan.json                         plan build. 프롬프트는 에이전트가 쓴다
  plans/plan-0001.json              plan build --replace가 보관한 이전 계획
  ledger.jsonl                      approve, jobs next, jobs done, jobs fail, assets next, assets done, assets fail
  takes/u01/t001/raw.mp4, meta.json jobs done
  takes/u01/t001/shots/0001.mov, conform.json     conform
  takes/u01/t001/scores.json        qc
  out/master.mov, final.mp4         assemble
  out/compare.mp4                   compare
  out/report.md                     report
```

`ledger.jsonl`, `assets/`, `takes/`, `plans/`는 도구만 쓴다. 에이전트가 자기 용도로 만들어도 되는 폴더는 `swap/`(사용자가 준 교체 이미지), `downloads/`(생성 결과를 내려받는 곳), `review/`(검수용 스틸)다.

## 명령

### init

프로젝트 폴더를 만들고 참조 영상을 그 안으로 복사한다.

```powershell
& $cr init $p --reference "D:\footage\reference.mp4" --name my-project
```

- 쓰는 것: `project.json`, `reference/source.<확장자>`.
- 출력: `project created: <폴더>`.
- 무엇이든 만들기 전에 참조 영상을 ffprobe로 읽어 본다. 거부되면 프로젝트 폴더는 생기지 않는다.
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

- 쓰는 것: `analysis/video.json`(크기, 프레임 속도, 프레임 수, 화면 영역 `picture_box`와 그 출처 `picture_source`), `analysis/cuts.json`(컷 목록과 출처), `analysis/shots/NNNN/`의 `ref.mp4`, `sheet.json`, `stills/`.
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
- 쓰는 것: `assets/<요소>/<자료 id>_<번호>.<확장자>`, `assets.json`, `ledger.jsonl`. 있던 파일은 덮어쓰지 않는다.
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
& $cr plan build $p --model seedance_2_5 --method edit --batch draft-1
& $cr plan build $p --model seedance_2_5 --method regen --batch cmp-regen --max-unit-seconds 2 --replace
```

- `--method edit`: 단위의 참조 클립(역할 `video`)과 교체 이미지를 입력으로 삼는다. 모델의 `video_ref`가 `true`여야 한다. `--method regen`: 단위 첫 숏의 첫 스틸(역할 `image`)과 교체 이미지를 입력으로 삼는다.
- `--quality`: 처음 만드는 작업의 품질. 기본 `draft`.
- `--max-unit-seconds`: 단위 길이의 상한(초). 생략하면 모델의 `max_seconds`. 이어지는 숏을 합친 길이가 상한 이하인 동안 한 단위에 담는다.
- `--replace`: 계획이 이미 있을 때 다시 만든다. 이전 계획은 `plans/plan-NNNN.json`으로 보관되고, 새 단위와 작업은 지금까지 쓴 적 없는 id를 받는다.
- 쓰는 것: `plan.json`, `analysis/units/<단위>/ref.mp4`.
- 출력: `u01: shots [1, 2, 3]  frames 0-90  duration 4 s` 같은 단위별 줄과 `planned jobs: j0001 (batch draft-1)`.
- 단위의 `duration`은 단위 길이를 초로 올림한 값이고 모델의 `min_seconds`보다 작지 않다. 그래서 테이크가 단위보다 길 수 있으며, `conform`이 필요한 프레임만 쓴다.
- 거부: `cast check`나 `models check`를 통과하지 못한다. 영상 모델이 아니다. `edit`인데 모델이 참조 영상을 받지 않는다. 숏 하나가 상한보다 길다. 단위에 필요한 이미지가 모델의 `image_refs`보다 많다. 계획이 있는데 `--replace`가 없다. 내보냈지만 끝나지 않은 작업이 있다(`jobs done`이나 `jobs fail`로 먼저 닫는다).

### plan check

계획의 작업을 내보낼 수 있는지 검사한다.

```powershell
& $cr plan check $p
```

- 출력: `plan ok`. 문제가 있으면 문제마다 한 줄씩 내고 종료 코드 2.
- 검사하는 것: 단위의 프롬프트가 비어 있지 않은 영어인지, 입력 파일이 있는지, 길이와 화면비와 해상도가 모델 범위 안인지, 견적 뒤에 `params`나 입력이 바뀌지 않았는지.
- 형식: 단위 id가 `u`와 숫자(`u01`), 작업 id가 `j`와 숫자(`j0001`)인지, 단위의 `method`가 `edit`나 `regen`인지, `shots`가 비어 있지 않은 숏 번호 목록인지, `qc_skip`이 `camera`, `motion`, `color`, `flicker_ratio` 가운데 이름을 담은 목록인지. 단위 id는 폴더 이름이 되므로, 형식이 틀린 단위는 `conform`, `qc`, `assemble`, `report`도 거부한다.
- 입력: 단위의 `inputs`가 지금의 교체표와 방법으로 정해지는 입력(맨 앞에 `edit`는 단위의 참조 클립, `regen`은 첫 숏의 첫 스틸, 이어서 단위의 숏에 나오는 요소의 교체 이미지가 요소 목록 순서로)과 하나도 다르지 않은지. 빠지거나 더해지거나 순서나 경로나 역할이나 요소가 다르면 `plan build --replace`로 계획을 다시 만들라는 문제가 나온다. 교체표가 `cast check`를 통과하지 못하면 입력을 비교할 수 없으므로 그것도 문제로 나온다.
- 모델: 이미지 입력 수가 지금 `models.json`의 `image_refs` 이하인지, `edit`이면 모델의 `video_ref`가 `true`인지.

### plan show

`plan.json`을 그대로 보여 준다. 단위의 `shots`, `start`, `end`, `method`, `model`, `params`, `prompt`, `inputs`, `qc_skip`, `selected_take`와 작업 목록이 있다.

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

- `--quality draft`는 `models.json`에 적힌 그 모델의 첫 해상도, `final`은 마지막 해상도로 나간다.
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

묶음의 승인 한도를 장부에 기록한다. 사용자가 금액을 승인한 뒤에만 실행한다.

```powershell
& $cr approve $p --batch draft-1 --limit 80 --by owner --note "drafts for u01, one retry included"
```

- 쓰는 것: `ledger.jsonl`. 출력: `approved batch draft-1: limit 80.0 credits (by owner)`.
- 같은 묶음에 다시 실행하면 한도가 더해진다. 추가 승인에는 추가분만 적는다.
- 거부: 장부가 손상돼 있다(`ledger line N is invalid`). 손상된 장부에는 아무것도 덧붙이지 않는다.

### jobs next

묶음에서 견적이 있는 작업을 작업 번호 순서로 장부에 올리고, 실행할 내용을 낸다.

```powershell
& $cr jobs next $p --batch draft-1
```

- 출력: JSON 목록. 항목마다 `job_id`, `unit`, `tool`(`generate_video`), `params`, `medias`(입력 파일의 절대 경로와 역할 `video` 또는 `image`). 내보낼 것이 없으면 `[]`.
- 견적이 없는 작업(`planned`)은 나가지 않는다. 한 번 내보낸 작업은 다시 나오지 않는다.
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
- 거부: 내보낸 상태(`issued`)가 아니다. 파일이 없다. 프로젝트의 `reference/`, `analysis/`, `takes/`, `out/`, `assets/` 안에 있는 파일이다(링크와 `..`을 따라간 실제 위치로 보고, 대소문자가 달라도 같은 폴더로 본다). 그런 파일은 프로젝트 자료이지 생성 결과가 아니다. 생성 작업에서 내려받은 파일만 등록한다.

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

- 출력: JSON. `ok`, `problems`, `batches`(묶음마다 `limit`, `committed`, `remaining`), `provider_ids`(끝난 작업의 Higgsfield 작업 id).
- `ok`가 `false`면 종료 코드 2. `problems`에 `ledger line 16 is invalid: ...`처럼 문제 줄이 적힌다. UTF-8로 읽을 수 없는 줄도 문제 줄 하나로 적히고 나머지 줄은 그대로 읽힌다.

### conform

테이크를 원본에 맞춘다. 프레임 속도를 원본으로 바꾸고, 화면 영역을 채우도록 크기를 맞춰 넘치는 부분을 잘라 내고, 원본과 같은 검은 띠를 붙인 뒤, 숏마다 원본과 같은 프레임 수로 자른다.

```powershell
& $cr conform $p --unit u01 --take t001
& $cr conform $p --unit u01 --take t001 --start-frame 3
& $cr conform $p --unit u01 --take t001 --allow-retime
```

- 쓰는 것: `takes/<단위>/<테이크>/shots/NNNN.mov`(ProRes), `conform.json`(`start_frame`, `retimed`, `source_frames`, `unit_frames`). 다시 실행하면 새로 만든다. `raw` 파일은 읽기만 한다.
- 출력: 숏 클립의 경로를 한 줄에 하나씩.
- `--start-frame`: 프레임 속도를 바꾼 뒤의 테이크에서 단위의 첫 프레임이 될 프레임 번호. 기본 0.
- `--allow-retime`: 테이크가 단위보다 짧을 때 고르게 늘려 프레임 수를 맞춘다. 동작 속도가 달라진다.
- 거부: 테이크가 짧다(`take too short: have 20 frames, need 30`). 그런 테이크가 없다. 현재 계획에 없는 단위다.

### qc

맞춘 테이크를 원본과 비교해 숏마다 점수를 낸다.

```powershell
& $cr qc $p --unit u01 --take t001
```

- 쓰는 것: `takes/<단위>/<테이크>/scores.json`. 출력도 같은 JSON이다(아래 "점수 읽기").
- 거부: `conform`을 하지 않았다.

### takes select

단위에서 완성본에 쓸 테이크를 고른다. 다시 실행하면 선택이 바뀐다.

```powershell
& $cr takes select $p --unit u01 --take t001
```

- 쓰는 것: `plan.json`의 `selected_take`. 출력: `u01: selected take t001`.

### assemble

단위마다 고른 테이크의 숏 클립을 원본 순서대로 이어 붙인다.

```powershell
& $cr assemble $p
& $cr assemble $p --audio "$p\audio\new_score.wav"
```

- 쓰는 것: `out/master.mov`(ProRes), `out/final.mp4`. 출력: `final.mp4`의 경로.
- `--audio`: 완성본에 넣을 오디오 파일. 원본의 소리는 쓰지 않는다. 생략하면 소리 없는 영상이 된다.
- 거부: 고른 테이크가 없는 단위가 있다. 고른 테이크를 `conform`하지 않았다. 이어 붙인 프레임 수가 원본과 다르다. `--audio`가 프로젝트의 `reference/`나 `analysis/` 안에 있는 파일이거나, 바이트가 참조 영상과 같은 파일이다(`init`에 준 원본 파일도 여기서 걸린다).

### compare

원본과 완성본을 좌우로 나란히 놓은 영상을 만든다.

```powershell
& $cr compare $p
```

- 쓰는 것: `out/compare.mp4`. 왼쪽이 원본, 오른쪽이 완성본이고 원본에 소리가 있으면 그 소리가 들어간다. 검토용이다.
- 거부: `out/final.mp4`가 없다.

### report

한국어 보고서를 쓴다.

```powershell
& $cr report $p
```

- 쓰는 것: `out/report.md`. 요약, 권리 확인, 교체표, 숏별 결과, 비용, 열린 항목(고른 테이크가 없는 단위, 판정이 `retry`인 숏, 고른 테이크에서 `overlap_ratio`가 0.98 이상이라 화면이 원본과 거의 같은 숏). 마지막 항목은 판정을 바꾸지 않는다. 원본을 복사한 화면이 아닌지 사용자가 볼 목록이다.
- 계획의 일부만 끝난 상태에서도 쓸 수 있다.

## 파일 형식

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
| `swaps[].images` | 교체 이미지. 한 장 이상이고, 상대 경로는 프로젝트 폴더 기준이다 |
| `swaps[].note` | 메모. 생략해도 된다 |

교체표에 없는 요소(위의 `dragon`)는 원본과 같게 재현한다. 어떤 단위의 숏에 나오는 요소의 교체 이미지는 모두 그 단위의 입력으로 들어가며, 순서는 요소 목록의 순서다.

### plan.json에서 에이전트가 고치는 곳

`plan.json`은 `plan build`가 만들고, 에이전트는 단위의 다음 세 곳만 고친다.

| 키 | 고치는 때 |
| --- | --- |
| `units[].prompt` | 언제나. 영어 프롬프트를 쓴다. 견적에 묶이지 않으므로 견적 뒤에 고쳐도 된다. 이미 내보낸 작업에는 내보낼 때의 프롬프트가 쓰인다 |
| `units[].qc_skip` | 교체 때문에 당연히 달라지는 점수를 판정에서 뺄 때. `"camera"`, `"motion"`, `"color"`, `"flicker_ratio"` 가운데 적는다. 넣기 전에 사용자에게 알린다 |
| `units[].params.duration` | 모델이 정해진 길이만 받을 때. 단위 길이보다 짧지 않은 허용 값으로 고친다. 견적 전에 고치고, 견적 뒤에 고쳤으면 다시 견적을 기록한다 |

작업 목록, `state`, `est_credits`, `inputs`, `selected_take`는 손으로 고치지 않는다. 입력을 바꾸려면 `casting.json`을 고치고 `plan build --replace`를 한다. `inputs`가 지금의 교체표와 다르면 `plan check`와 `jobs next`가 거부한다.

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
| `verdict`, `failed` | 그 숏의 판정과 기준에 못 미친 항목 | |

## 검수용 스틸

영상은 스틸 띠로 만들어서 본다. 결과 이미지는 프로젝트의 `review/` 폴더에 둔다. 폴더는 먼저 만든다.

```powershell
New-Item -ItemType Directory -Force "$p\review" | Out-Null
```

**맞추기 전의 테이크.** 첫 프레임부터 12프레임마다 한 장씩, 열여섯 칸까지 뽑는다. 첫 칸이 테이크의 첫 프레임이다. 올바른 숏인지, 첫 프레임이 원본의 첫 프레임과 같은 순간인지 본다. 테이크가 더 길면 간격 `12`를 늘린다.

```powershell
ffmpeg -hide_banner -loglevel error -y -i "$p\takes\u01\t001\raw.mp4" -vf "select='not(mod(n\,12))',scale=320:-2,tile=8x2" -frames:v 1 -update 1 "$p\review\u01_t001_raw.png"
```

**맞춘 숏 클립.** 첫 프레임, 끝 프레임, 그 사이를 고르게 뽑는다. `5`는 간격으로 숏의 프레임 수를 8로 나눠 올림한 값이고, `35`는 끝 프레임 번호로 프레임 수에서 1을 뺀 값이다. 숏의 프레임 수는 `casting.template.json`의 `start`와 `end`의 차다. 원본의 `analysis/review/shot_NNNN.png`, 스틸 `first.png`와 `last.png`와 나란히 놓고 본다.

```powershell
ffmpeg -hide_banner -loglevel error -y -i "$p\takes\u01\t001\shots\0003.mov" -vf "select='not(mod(n\,5))+eq(n\,35)',scale=320:-2,tile=9x1" -frames:v 1 -update 1 "$p\review\u01_t001_0003.png"
```

**비교 영상.** 칸마다 왼쪽이 원본, 오른쪽이 결과다. 첫 프레임부터 12프레임마다 한 장씩, 열여섯 칸까지 뽑는다. 영상이 더 길면 간격 `12`를 늘리거나 `tile`의 줄 수를 늘린다.

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

어떤 모델과 방법이 이 영상에 맞는지 모를 때, 숏 한두 개를 후보마다 만들어 비교한다. 한 프로젝트 안에서 후보를 차례로 바꿔 가며 한다.

1. `models.json`에 후보 모델을 모두 적는다.
2. 첫 후보로 계획을 만든다. 숏을 따로따로 시험하려면 `--max-unit-seconds`로 단위를 짧게 한다.
3. 시험할 단위의 작업에만 `jobs set-cost`를 한다. 견적이 없는 작업은 나가지 않으므로 나머지 단위에는 비용이 들지 않는다.
4. 비용 표를 보여 주고 승인받은 뒤 `jobs next`, 생성, `jobs done`, `conform`, `qc`, 스틸 검수까지 마친다. 점수는 `takes/<단위>/<테이크>/scores.json`에 남는다.
5. 다음 후보로 계획을 바꾼다. 묶음 이름은 후보마다 다르게 한다.

   ```powershell
   & $cr plan build $p --model seedance_2_5 --method regen --batch cmp-regen --replace
   ```

   이전 계획은 `plans/plan-0001.json`으로 보관되고, 새 단위와 작업은 이어지는 번호(`u02`, `j0002`)를 받는다. 이전 단위의 테이크와 점수 파일은 `takes/`에 그대로 남는다. 다만 이전 단위는 현재 계획에 없으므로 `conform`과 `qc`를 더 할 수 없다. 계획을 바꾸기 전에 끝낸다.
6. 내보냈지만 끝나지 않은 작업이 있으면 `--replace`가 거부된다. `jobs done`이나 `jobs fail`로 닫고 다시 한다.
7. 후보, 숏, 점수, 눈으로 본 평가, 쓴 크레딧을 표로 사용자에게 보여 주고 기본 방법과 모델을 정한다. 어느 단위가 어느 모델이었는지는 보관된 계획 파일의 `units[].model`과 `units[].method`에 있다.
8. 정한 방법과 모델로 `plan build --replace`를 해서 전체 계획을 만들고 SKILL.md의 4단계부터 이어 간다.
