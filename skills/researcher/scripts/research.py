#!/usr/bin/env python3
"""Create research files and check local data integrity; Python 3.10+, stdlib only."""

import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

HEADERS = {
    "sources.csv": "source_id,title,url_or_path,publisher,source_type,published_at,accessed_at,access_status,notes".split(","),
    "evidence.csv": "evidence_id,claim,status,source_ids,locator,support,checked_at,valid_as_of,assumptions,based_on,notes".split(","),
    "comparisons.csv": "item_id,criterion_id,value,unit,scope,as_of,evidence_ids,status,notes".split(","),
}
MD_FILES = ("brief.md", "task_plan.md", "progress.md", "findings.md", "report.md", "decision.md")
EVIDENCE_STATUS = {"подтверждено", "заявление поставщика", "расчёт", "предположение", "не проверено"}
CELL_STATUS = {"заполнено", "не проверено", "противоречие", "не применимо"}
SOURCE_TYPES = {"primary", "vendor", "secondary", "internal", "memory"}
ACCESS_STATUS = {"доступен", "частично", "недоступен"}


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def split_ids(value):
    return [part.strip() for part in value.split(";") if part.strip()]


def init_study(root, title, question):
    templates = Path(__file__).resolve().parent.parent / "assets" / "study"
    values = {"TITLE": title, "QUESTION": question, "DATE": date.today().isoformat()}
    # Read templates before making any changes; never overwrite an existing study.
    rendered = {
        name: re.sub(r"\{\{(TITLE|QUESTION|DATE)\}\}", lambda m: values[m[1]],
                     (templates / name).read_text(encoding="utf-8"))
        for name in MD_FILES
    }
    root.mkdir(parents=True, exist_ok=False)
    for name, content in rendered.items():
        with (root / name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    for name, header in HEADERS.items():
        with (root / name).open("x", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle).writerow(header)
    manifest = {"schema_version": 1, "title": title, "question": question,
                "created_at": values["DATE"], "items": [], "criteria": []}
    with (root / "research.json").open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    emit({"created": str(root.resolve()), "files": sorted([*MD_FILES, *HEADERS, "research.json"])})


def check_study(root, final=False):
    errors, warnings = [], []

    def issue(message, strict=False):
        (errors if strict or final else warnings).append(message)

    def valid_date(value, label, required=False):
        if not value:
            if required:
                errors.append(f"{label}: требуется дата")
            return
        try:
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError
            date.fromisoformat(value)
        except ValueError:
            errors.append(f"{label}: ожидается существующая дата YYYY-MM-DD")

    def index(rows, key, label):
        indexed = {}
        for n, row in enumerate(rows, 1):
            ident = row.get(key, "") if isinstance(row, dict) else ""
            if not isinstance(ident, str) or not ident.strip() or ident != ident.strip() or ";" in ident:
                errors.append(f"{label}, запись {n}: некорректный {key}")
            elif ident in indexed:
                errors.append(f"{label}: повтор ID {ident}")
            else:
                indexed[ident] = row
        return indexed

    def require(row, fields, label):
        for field in fields:
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(f"{label}: требуется {field}")

    def csv_rows(name):
        try:
            with (root / name).open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, strict=True)
                if reader.fieldnames != HEADERS[name]:
                    errors.append(f"{name}: неверные заголовки; ожидаются {HEADERS[name]}")
                    return []
                rows = []
                for n, row in enumerate(reader, 2):
                    if None in row or any(value is None for value in row.values()):
                        errors.append(f"{name}, запись {n}: неверное число полей")
                        continue
                    rows.append({key: value.strip() for key, value in row.items()})
                return rows
        except (OSError, UnicodeError, csv.Error) as exc:
            errors.append(f"{name}: {exc}")
            return []

    documents = {}
    for name in MD_FILES:
        try:
            documents[name] = (root / name).read_text(encoding="utf-8-sig")
            if not documents[name].strip():
                errors.append(f"{name}: пустой файл")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{name}: {exc}")
    try:
        manifest = json.loads((root / "research.json").read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            raise ValueError("ожидается объект JSON")
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(f"research.json: {exc}")
        manifest = {}
    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1:
        errors.append("research.json: ожидается schema_version 1")
    require(manifest, ("title", "question"), "research.json")
    valid_date(manifest.get("created_at"), "research.json.created_at", True)
    definitions = {}
    for key in ("items", "criteria"):
        rows = manifest.get(key, [])
        if not isinstance(rows, list):
            errors.append(f"research.json.{key}: ожидается массив")
            rows = []
        definitions[key] = index(rows, "id", key)
        if not rows:
            issue(f"research.json.{key}: список пуст")
        for ident, row in definitions[key].items():
            require(row, ("name",), ident)
            if key == "criteria":
                if type(row.get("required")) is not bool:
                    errors.append(f"{ident}: required должен быть true или false")
                if not isinstance(row.get("unit"), str):
                    errors.append(f"{ident}: unit должна быть строкой (допустима пустая)")

    sources = index(csv_rows("sources.csv"), "source_id", "sources.csv")
    evidence = index(csv_rows("evidence.csv"), "evidence_id", "evidence.csv")
    cells = csv_rows("comparisons.csv")
    for ident, row in sources.items():
        require(row, ("title", "url_or_path"), ident)
        if row["source_type"] not in SOURCE_TYPES:
            errors.append(f"{ident}: неизвестный source_type")
        if row["access_status"] not in ACCESS_STATUS:
            errors.append(f"{ident}: неизвестный access_status")
        valid_date(row["published_at"], f"{ident}.published_at")
        valid_date(row["accessed_at"], f"{ident}.accessed_at", True)
    dependencies = {}
    for ident, row in evidence.items():
        require(row, ("claim",), ident)
        status = row["status"]
        if status not in EVIDENCE_STATUS:
            errors.append(f"{ident}: неизвестный статус доказательства")
        valid_date(row["checked_at"], f"{ident}.checked_at", status != "не проверено")
        valid_date(row["valid_as_of"], f"{ident}.valid_as_of")
        source_ids = split_ids(row["source_ids"])
        dependencies[ident] = split_ids(row["based_on"])
        for sid in source_ids:
            if sid not in sources:
                errors.append(f"{ident}: отсутствует источник {sid}")
        for eid in dependencies[ident]:
            if eid not in evidence:
                errors.append(f"{ident}: отсутствует входное доказательство {eid}")
        if status in {"подтверждено", "заявление поставщика"}:
            require(row, ("source_ids", "locator", "support"), ident)
            accessible = [sources[sid] for sid in source_ids if sid in sources
                          and sources[sid]["access_status"] in {"доступен", "частично"}]
            if not accessible:
                errors.append(f"{ident}: нет прочитанного источника для заявленного статуса")
            elif all(source["source_type"] == "memory" for source in accessible):
                errors.append(f"{ident}: память не заменяет исходное доказательство")
            elif status == "подтверждено" and all(source["source_type"] == "vendor" for source in accessible):
                warnings.append(f"{ident}: только поставщик; проверь необходимость статуса «заявление поставщика»")
        if status in {"расчёт", "предположение"}:
            require(row, ("assumptions",), ident)
        if status == "расчёт":
            require(row, ("support", "based_on"), ident)

    # Topological traversal detects cycles without recursion limits on long studies.
    unresolved, pending = {}, set(evidence)
    while pending:
        ready = {eid for eid in pending if not any(dep in pending for dep in dependencies[eid])}
        if not ready:
            errors.append("Цикл зависимостей evidence.based_on; затронуты: " + ";".join(sorted(pending)))
            unresolved.update({eid: True for eid in pending})
            break
        for eid in ready:
            unresolved[eid] = evidence[eid]["status"] in {"предположение", "не проверено"} or any(
                unresolved.get(dep, True) for dep in dependencies[eid])
        pending -= ready

    expected = {(item, criterion) for item in definitions["items"] for criterion in definitions["criteria"]}
    seen, filled = set(), 0
    for n, row in enumerate(cells, 2):
        pair = (row["item_id"], row["criterion_id"])
        label = f"comparisons.csv, запись {n} ({pair[0]}/{pair[1]})"
        if pair not in expected:
            errors.append(f"{label}: объект или критерий отсутствует в research.json")
        if pair in seen:
            errors.append(f"{label}: повтор пары объект–критерий")
        seen.add(pair)
        criterion = definitions["criteria"].get(pair[1], {})
        if row["status"] not in CELL_STATUS:
            errors.append(f"{label}: неизвестный статус клетки")
        valid_date(row["as_of"], f"{label}.as_of")
        eids = split_ids(row["evidence_ids"])
        for eid in eids:
            if eid not in evidence:
                errors.append(f"{label}: отсутствует доказательство {eid}")
        uncertain = row["status"] in {"не проверено", "противоречие"}
        if row["status"] == "заполнено":
            require(row, ("value", "scope", "evidence_ids"), label)
            if criterion.get("unit") and row["unit"] != criterion["unit"]:
                errors.append(f"{label}: единица отличается от критерия {criterion['unit']}")
            uncertain = not eids or any(unresolved.get(eid, True) for eid in eids)
            if not uncertain:
                filled += 1
        else:
            require(row, ("notes",), label)
        if row["status"] == "противоречие" and len(set(eids)) < 2:
            errors.append(f"{label}: для противоречия нужны минимум два E-ID")
        if row["status"] == "не применимо" and criterion.get("required"):
            warnings.append(f"{label}: вручную проверь неприменимость обязательного критерия")
        if uncertain:
            message = f"{label}: результат остаётся неопределённым"
            if criterion.get("required"):
                issue(message + " по обязательному критерию")
            else:
                warnings.append(message)
    missing = sorted(expected - seen)
    if missing:
        issue("Пропущены клетки: " + ", ".join(f"{i}/{c}" for i, c in missing))
    if final:
        if not sources or not evidence:
            errors.append("Для итогового исследования нужны источники и доказательства")
        report = documents.get("report.md", "")
        if not re.match(r"\A---\s*\nstatus: complete\s*\n---", report):
            errors.append("report.md: полный результат требует frontmatter status: complete")
        if "<!-- researcher:unwritten -->" in report or len(report.split("---", 2)[-1].strip()) < 80:
            errors.append("report.md: итоговый текст не заполнен")
    return {
        "study": str(root.resolve()), "mode": "final" if final else "working",
        "structural_check": "passed" if not errors else "failed",
        "errors": errors, "warnings": warnings,
        "coverage": {"expected": len(expected), "present": len(expected & seen),
                     "filled_with_resolved_evidence": filled, "missing": [list(p) for p in missing]},
        "limits": "Проверены структура и ссылки ID, не истинность, актуальность, формулы или смысл отчёта.",
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Создать новое исследование без перезаписи")
    init.add_argument("directory", type=Path)
    init.add_argument("--title", required=True)
    init.add_argument("--question", required=True)
    check = commands.add_parser("check", help="Проверить структуру локального исследования")
    check.add_argument("directory", type=Path)
    check.add_argument("--final", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "init":
            if not args.title.strip() or not args.question.strip():
                parser.error("title и question не могут быть пустыми")
            init_study(args.directory, args.title, args.question)
            return 0
        result = check_study(args.directory, args.final)
        emit(result)
        return 1 if result["errors"] else 0
    except (OSError, UnicodeError, ValueError) as exc:
        emit({"error": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
