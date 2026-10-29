"""Types shared by the planner, collector and persisted JSON records."""
from __future__ import annotations

from typing import Literal, NotRequired, TypeAlias, TypedDict

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
JSONObject: TypeAlias = dict[str, JSONValue]
FingerprintField: TypeAlias = Literal[
    "tlsClientCiphersSha1", "tlsClientExtensionsSha1", "tlsClientExtensionsSha1Le"
]
Fingerprint: TypeAlias = dict[str, str]


class CaseIdentity(TypedDict):
    target: str
    language: str
    client: str
    runtime_version: str
    client_version: str
    platform: str
    runner: str
    revision: str
    verify: NotRequired[Literal[False]]


class Case(CaseIdentity):
    id: str


class Job(TypedDict):
    case: Case
    cases: list[Case]
    id: str


class VersionSource(TypedDict):
    type: str
    versions: NotRequired[list[str]]
    url: NotRequired[str]
    items_path: NotRequired[str]
    version_key: NotRequired[str]
    platform: NotRequired[str]
    platform_version: NotRequired[str]
    arch: NotRequired[str]
    package: NotRequired[str]
    group: NotRequired[str]
    artifact: NotRequired[str]
    repo: NotRequired[str]
    tag_prefix: NotRequired[str]
    max_pages: NotRequired[int]
    min_version: NotRequired[str]
    max_version: NotRequired[str]
    allow_prerelease: NotRequired[bool]


class Adapter(TypedDict):
    probe: list[str]
    prepare: NotRequired[list[str]]
    env: NotRequired[dict[str, str]]


class Target(TypedDict):
    id: str
    language: str
    client: str
    runtime_versions: VersionSource
    client_versions: VersionSource
    adapter: Adapter
    enabled: NotRequired[bool]
    platform: NotRequired[str]
    runner: NotRequired[str]
    revision: NotRequired[str | int]
    compatibility: NotRequired[dict[str, list[dict[str, str]]]]
    historical_baseline_client: NotRequired[str]
    historical_anchor_runtime: NotRequired[str]
    recheck_modes: NotRequired[list[str]]
    capture_profile: NotRequired[str]


class Config(TypedDict):
    schema_version: int
    endpoint: str
    targets: list[Target]
    sampling: dict[str, int]
    execution: dict[str, int]
    profiles: dict[str, dict[str, bool]]
    tls_verify_modes: NotRequired[list[Literal["default", "disabled"]]]


class TLSBackend(TypedDict):
    name: str
    version: str


class Environment(TypedDict):
    os: str
    arch: str
    tls_backend: TLSBackend
    image: NotRequired[str]
    dependencies: NotRequired[dict[str, str]]
    runtime_resolved_version: NotRequired[str]
    capture_profile: NotRequired[str]


class RecordIdentity(CaseIdentity):
    environment: Environment


class TLSMetadata(TypedDict, total=False):
    tlsClientHelloLength: int
    tlsCipher: str
    tlsVersion: str
    tlsAlpn: str


class JointSample(TLSMetadata):
    tlsClientCiphersSha1: str
    tlsClientExtensionsSha1: str
    tlsClientExtensionsSha1Le: str


class Evidence(JointSample):
    count: int


class Record(TypedDict):
    schema_version: int
    case_id: str
    record_id: str
    identity: RecordIdentity
    observations: list[Fingerprint]
    evidence: NotRequired[list[Evidence]]
    evidence_truncated: NotRequired[bool]


class IndexClient(TypedDict):
    target: str
    language: str
    client: str
    runtime_version: str
    client_version: str
    platform: str
    environment: Environment
    verify: NotRequired[Literal[False]]


class IndexEntry(IndexClient, TLSMetadata, total=False):
    tlsClientCiphersSha1: str
    tlsClientExtensionsSha1: str
    tlsClientExtensionsSha1Le: str
    sample_count: int


class CandidateIdentity(TypedDict):
    target: str | None
    language: str | None
    client: str | None
    runtime_version: str | None
    client_version: str | None
    platform: str | None
    environment: Environment | None
    verify: NotRequired[Literal[False]]


class MatchDetail(TLSMetadata, total=False):
    tlsClientCiphersSha1: str
    tlsClientExtensionsSha1: str
    tlsClientExtensionsSha1Le: str
    matched_field_names: list[str]


class Candidate(CandidateIdentity):
    match: str
    matched_fields: int
    supplied_fields: int
    sample_count: int
    details: list[MatchDetail]
