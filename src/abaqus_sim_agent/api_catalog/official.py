from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import lxml.etree as etree

from .models import ApiEntityKind, ApiParameter, ApiSourceRef, OfficialApiRecord

SCRIPTING_GUIDE_ID = "SIMACAEKERRefMap"

_CLASS_TOKEN_XPATH = 'contains(concat(" ", normalize-space(@class), " "), " {token} ")'
_RELEASE_PATTERN = re.compile(r"\b20\d{2}\s+(?:GA|FD\d{2})\b")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_CALL_SUFFIX_PATTERN = re.compile(r"\s*\(\.\.\.\)\s*$")


class AbaqusScriptingReferenceError(RuntimeError):
    pass


class AbaqusScriptingReferenceLoader:
    """把 Scripting Reference 的 callable article 解析成原子 API 记录。"""

    def __init__(
        self,
        docs_root: str | Path,
        *,
        docs_base_url: str = "http://localhost:4040/English",
    ) -> None:
        self.guide_root, self.language_root = _resolve_guide_root(Path(docs_root))
        self.docs_base_url = docs_base_url.rstrip("/")
        self.release = _detect_release(self.language_root)
        self._parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            recover=False,
        )

    def lazy_load(self) -> Iterator[OfficialApiRecord]:
        for path in sorted(self.guide_root.glob("*.htm")):
            if path.name.casefold() == "sitemap.htm":
                continue
            yield from self._load_page(path)

    def load(self) -> list[OfficialApiRecord]:
        return list(self.lazy_load())

    def _load_page(self, path: Path) -> list[OfficialApiRecord]:
        try:
            tree = etree.parse(str(path), self._parser)
        except (OSError, etree.XMLSyntaxError) as exc:
            raise AbaqusScriptingReferenceError(
                f"failed to parse Abaqus Scripting XHTML: {path}"
            ) from exc

        document_id = _xpath_string(tree, './/meta[@name="DC.identifier"]/@content')
        title = _normalized_text(_xpath_string(tree, ".//title"))
        body_id = _xpath_string(tree, ".//body/@id")
        if not document_id or not title or body_id != document_id:
            raise AbaqusScriptingReferenceError(
                f"Abaqus Scripting page lacks a stable identifier/title contract: {path}"
            )

        owner_name = re.sub(r"\s+object$", "", title, flags=re.IGNORECASE).strip()
        access = _page_access(tree)
        relative_path = path.relative_to(self.language_root).as_posix()
        page_url = f"{self.docs_base_url}/{relative_path}"
        records: list[OfficialApiRecord] = []
        selectors: dict[str, ApiEntityKind] = {
            "pymethodsect": "method",
            "pyconstructsect": "constructor",
            "pycmdmethodsect": "command",
        }
        for class_token, entity_kind in selectors.items():
            expression = f".//article[{_CLASS_TOKEN_XPATH.format(token=class_token)}]"
            for article in tree.xpath(expression):
                if not isinstance(article, etree._Element):
                    continue
                article_id = str(article.get("id", "")).strip()
                callable_name = _callable_name(article)
                if not article_id or not callable_name:
                    raise AbaqusScriptingReferenceError(
                        f"callable article lacks id/name in {path}"
                    )
                paths = _article_paths(article)
                derived_names = _derived_public_names(
                    paths,
                    owner_name=owner_name,
                    callable_name=callable_name,
                    entity_kind=entity_kind,
                )
                owner_qualified = f"{owner_name}.{callable_name}"
                public_names = _unique((*derived_names, owner_qualified))
                canonical_name = public_names[0]
                aliases = (
                    (owner_name,)
                    if (
                        entity_kind == "constructor"
                        and owner_name == callable_name
                        and f"{owner_name}.__init__" in derived_names
                    )
                    else ()
                )
                records.append(
                    OfficialApiRecord(
                        record_id=(
                            f"official:{self.release}:{document_id}#{article_id}"
                        ),
                        entity_kind=entity_kind,
                        owner_name=owner_name,
                        callable_name=callable_name,
                        canonical_name=canonical_name,
                        public_names=public_names,
                        aliases=aliases,
                        summary=_article_summary(article),
                        access=access,
                        paths=paths,
                        required_parameters=_article_parameters(
                            article,
                            required=True,
                        ),
                        optional_parameters=_article_parameters(
                            article,
                            required=False,
                        ),
                        return_description=_named_section_text(
                            article,
                            "returnsect",
                            "Return value",
                        ),
                        exceptions=_exception_texts(article),
                        source=ApiSourceRef(
                            kind="official",
                            release=self.release,
                            source_path=relative_path,
                            source_url=f"{page_url}#{article_id}",
                            document_id=document_id,
                            section_id=article_id,
                        ),
                    )
                )
        return records


def load_official_records(
    docs_root: str | Path,
    *,
    docs_base_url: str = "http://localhost:4040/English",
) -> list[OfficialApiRecord]:
    return AbaqusScriptingReferenceLoader(
        docs_root,
        docs_base_url=docs_base_url,
    ).load()


def _resolve_guide_root(path: Path) -> tuple[Path, Path]:
    root = path.expanduser().resolve()
    candidates = (
        root,
        root / SCRIPTING_GUIDE_ID,
        root / "English" / SCRIPTING_GUIDE_ID,
    )
    guide_root = next(
        (
            candidate
            for candidate in candidates
            if candidate.is_dir() and candidate.name == SCRIPTING_GUIDE_ID
        ),
        None,
    )
    if guide_root is None:
        raise ValueError(f"Abaqus Scripting guide directory is missing below: {root}")
    return guide_root, guide_root.parent


def _detect_release(language_root: Path) -> str:
    legal = (
        language_root
        / "SIMULIA_Established_FrontmatterMap"
        / "sim-r-SIMULIA_EstablishedLegal.htm"
    )
    if not legal.is_file():
        return "unknown"
    text = legal.read_text(encoding="utf-8", errors="replace")
    match = _RELEASE_PATTERN.search(text)
    return match.group(0) if match else "unknown"


def _page_access(tree: etree._ElementTree) -> tuple[str, ...]:
    token = _CLASS_TOKEN_XPATH.format(token="pyaccess")
    sections = tree.xpath(f".//*[self::div or self::section][{token}]")
    values: list[str] = []
    for section in sections:
        for code in section.xpath('.//*[contains(@class, "codeblock")]'):
            text = _normalized_text(" ".join(code.itertext()))
            if text:
                values.append(text)
    return _unique(values)


def _callable_name(article: etree._Element) -> str:
    for token in ("pymethod", "pyctor", "pycmdmethod"):
        expression = f".//*[{_CLASS_TOKEN_XPATH.format(token=token)}][1]"
        nodes = article.xpath(expression)
        if nodes:
            value = _normalized_text(" ".join(nodes[0].itertext()))
            value = _CALL_SUFFIX_PATTERN.sub("", value)
            if value:
                return value
    headings = article.xpath("./h2[1]")
    if not headings:
        return ""
    value = _normalized_text(" ".join(headings[0].itertext()))
    return _CALL_SUFFIX_PATTERN.sub("", value)


def _article_summary(article: etree._Element) -> str:
    abstract_token = _CLASS_TOKEN_XPATH.format(token="abstract")
    nodes = article.xpath(f".//p[{abstract_token}][1]")
    if not nodes:
        return ""
    return _normalized_text(" ".join(nodes[0].itertext()))


def _article_paths(article: etree._Element) -> tuple[str, ...]:
    token = _CLASS_TOKEN_XPATH.format(token="pypath")
    sections = article.xpath(f".//*[self::div or self::section][{token}]")
    values: list[str] = []
    for section in sections:
        path_nodes = section.xpath('./p | ./pre | .//*[contains(@class, "codeblock")]')
        for path_node in path_nodes:
            text = _normalized_text(" ".join(path_node.itertext()))
            if text:
                values.append(text)
    return _unique(values)


def _article_parameters(
    article: etree._Element,
    *,
    required: bool,
) -> tuple[ApiParameter, ...]:
    marker = "arglistreq" if required else "arglistopt"
    sections = article.xpath(
        f'.//*[self::div or self::section][contains(@class, "{marker}")'
        f' and not(contains(@class, "no{marker}"))]'
    )
    parameters: list[ApiParameter] = []
    seen: set[str] = set()
    for section in sections:
        for term in section.xpath("./dl/dt"):
            name_nodes = term.xpath(
                f".//*[{_CLASS_TOKEN_XPATH.format(token='pyarg')}][1]"
            )
            name = _normalized_text(
                " ".join((name_nodes[0] if name_nodes else term).itertext())
            )
            if not name or name in seen:
                continue
            description_node = term.getnext()
            description = (
                _normalized_text(" ".join(description_node.itertext()))
                if description_node is not None
                and etree.QName(description_node).localname == "dd"
                else ""
            )
            parameters.append(
                ApiParameter(
                    name=name,
                    required=required,
                    description=description,
                )
            )
            seen.add(name)
    return tuple(parameters)


def _named_section_text(
    article: etree._Element,
    class_fragment: str,
    heading: str,
) -> str:
    nodes = article.xpath(
        f'.//*[self::div or self::section][contains(@class, "{class_fragment}")][1]'
    )
    if not nodes:
        return ""
    value = _normalized_text(" ".join(nodes[0].itertext()))
    if value.casefold().startswith(heading.casefold()):
        return value[len(heading) :].strip()
    return value


def _exception_texts(article: etree._Element) -> tuple[str, ...]:
    value = _named_section_text(article, "exceptsect", "Exceptions")
    if not value or value.casefold() == "none.":
        return ()
    return (value,)


def _derived_public_names(
    paths: tuple[str, ...],
    *,
    owner_name: str,
    callable_name: str,
    entity_kind: str,
) -> tuple[str, ...]:
    names: list[str] = []
    for path in paths:
        if ".rootAssembly." in path:
            names.append(f"Assembly.{callable_name}")
        elif re.search(r"\bmdb\.models\[[^]]+\]\.parts\[", path):
            names.append(f"Part.{callable_name}")
        elif re.search(r"\bmdb\.models\[[^]]+\]\.", path):
            names.append(f"Model.{callable_name}")
        elif path.startswith("session."):
            names.append(f"Session.{callable_name}")
        elif entity_kind == "command" and "." not in path:
            names.append(callable_name)
    if not names and entity_kind == "command":
        names.append(callable_name)
    if not names and entity_kind == "constructor" and owner_name == callable_name:
        names.append(f"{owner_name}.__init__")
    if not names:
        names.append(f"{owner_name}.{callable_name}")
    return _unique(names)


def _xpath_string(tree: etree._ElementTree, expression: str) -> str:
    value: Any = tree.xpath(f"string({expression})")
    return str(value).strip()


def _normalized_text(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip()


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))
