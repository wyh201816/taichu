"""五层上下文基准的密封压力计划与确定性种子生成。"""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from typing import TYPE_CHECKING, Any, Literal, Mapping, Self

from pydantic import Field, field_validator, model_validator

from taichu.application.evaluations.general_agent_benchmark.canonical import (
    canonical_sha256,
)
from taichu.application.evaluations.general_agent_benchmark.models import (
    BenchmarkModel,
    Sha256,
    StableId,
)

if TYPE_CHECKING:
    from taichu.application.evaluations.general_agent_benchmark.observations import (
        CaseObservation,
        EvidenceOwner,
        ObservedRecoveryDecision,
    )
    from taichu.application.evaluations.general_agent_benchmark.oracles import (
        ContextPreservationObservation,
        MemoryCarrierObservation,
        ResultContractEquivalenceObservation,
        ResultContractProjection,
    )
    from taichu.application.general_agent.pipeline import ContextCapacityError
    from taichu.application.general_agent.models import (
        GeneralAgentExecutionPlan,
        GeneralAgentContextSnapshot,
    )


class PressureKind(StrEnum):
    """只表达可复用载体，不携带案例编号或可执行表达式。"""

    HISTORY = "history"
    WORKING_MEMORY = "working_memory"
    NODE_OUTPUT = "node_output"
    MULTI_SOURCE = "multi_source"
    EQUIVALENCE_PAIR = "equivalence_pair"
    INVALID_MEMORY = "invalid_memory"
    CURRENT_REQUEST = "current_request"
    UNSAFE_TOTAL = "unsafe_total"

    # 语义别名供调用方使用；序列化仍只有上面的固定载体值。
    LONG_HISTORY = "history"
    LARGE_NODE_RESULT = "node_output"
    MULTI_SOURCE_OVERFLOW = "multi_source"
    LONG_CURRENT_REQUEST = "current_request"
    UNSAFE_COMPRESSION = "unsafe_total"


def _validate_fixture_blob_ref(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or value.endswith("/")
    ):
        raise ValueError("fixture blob ref 必须是无 .. 的相对 POSIX 文件引用。")
    return value


def _validate_canonical_refs(
    refs: tuple[str, ...],
    *,
    field_name: str,
    allow_empty: bool,
) -> None:
    if not allow_empty and not refs:
        raise ValueError(f"PressurePlan 规范化内容要求 {field_name} 非空。")
    if refs != tuple(sorted(set(refs))):
        raise ValueError(f"PressurePlan 规范化内容要求 {field_name} 排序且不得重复。")


class PressureFixtureBlob(BenchmarkModel):
    """由夹具清单定位的不可变压力文本块。"""

    schema_: Literal["taichu.general_agent_benchmark.pressure_fixture_blob@1"] = Field(
        alias="schema",
        default="taichu.general_agent_benchmark.pressure_fixture_blob@1",
    )
    blob_ref: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1, max_length=1_000_000)
    content_sha256: Sha256

    _blob_ref_is_safe = field_validator("blob_ref")(_validate_fixture_blob_ref)

    @model_validator(mode="after")
    def _content_is_sealed(self) -> Self:
        if self.content_sha256 != canonical_sha256(self.content):
            raise ValueError("压力 fixture blob 内容哈希不匹配。")
        return self

    @classmethod
    def seal(cls, *, blob_ref: str, content: str) -> PressureFixtureBlob:
        return cls(
            blob_ref=blob_ref,
            content=content,
            content_sha256=canonical_sha256(content),
        )


class PressurePlan(BenchmarkModel):
    """只声明通用载体规模、夹具引用和必须保护/排除的事实。"""

    schema_: Literal["taichu.general_agent_benchmark.pressure_plan@1"] = Field(
        alias="schema",
        default="taichu.general_agent_benchmark.pressure_plan@1",
    )
    plan_id: StableId
    kind: PressureKind
    fixture_blob_ref: str = Field(min_length=1, max_length=512)
    repetition_count: int = Field(gt=0, le=10_000)
    unit_size: int = Field(gt=0, le=100_000)
    protected_fact_refs: tuple[StableId, ...] = Field(min_length=1)
    invalid_sentinel_refs: tuple[StableId, ...] = ()
    paired_case_ref: StableId | None = None
    content_hash: Sha256

    _blob_ref_is_safe = field_validator("fixture_blob_ref")(_validate_fixture_blob_ref)

    @model_validator(mode="after")
    def _plan_is_canonical_and_sealed(self) -> Self:
        _validate_canonical_refs(
            self.protected_fact_refs,
            field_name="protected_fact_refs",
            allow_empty=False,
        )
        _validate_canonical_refs(
            self.invalid_sentinel_refs,
            field_name="invalid_sentinel_refs",
            allow_empty=True,
        )
        overlap = set(self.protected_fact_refs) & set(self.invalid_sentinel_refs)
        if overlap:
            raise ValueError("受保护事实与无效哨兵引用不得重叠。")
        if (self.kind is PressureKind.EQUIVALENCE_PAIR) != (
            self.paired_case_ref is not None
        ):
            raise ValueError(
                "只有 equivalence_pair 压力计划必须且可以声明 paired_case_ref。"
            )
        if (self.kind is PressureKind.INVALID_MEMORY) != bool(
            self.invalid_sentinel_refs
        ):
            raise ValueError(
                "只有 invalid_memory 压力计划必须且可以声明 invalid_sentinel_refs。"
            )
        if self.repetition_count * self.unit_size > 10_000_000:
            raise ValueError("单个 PressurePlan 的确定性展开规模超过安全上限。")
        payload = self.model_dump(
            mode="python",
            by_alias=True,
            exclude={"content_hash"},
        )
        if self.content_hash != canonical_sha256(payload):
            raise ValueError("PressurePlan content_hash 与规范化内容不一致。")
        return self

    @classmethod
    def seal(
        cls,
        *,
        plan_id: str,
        kind: PressureKind,
        fixture_blob_ref: str,
        repetition_count: int,
        unit_size: int,
        protected_fact_refs: tuple[str, ...],
        invalid_sentinel_refs: tuple[str, ...] = (),
        paired_case_ref: str | None = None,
    ) -> PressurePlan:
        payload = {
            "schema": "taichu.general_agent_benchmark.pressure_plan@1",
            "plan_id": plan_id,
            "kind": kind,
            "fixture_blob_ref": fixture_blob_ref,
            "repetition_count": repetition_count,
            "unit_size": unit_size,
            "protected_fact_refs": protected_fact_refs,
            "invalid_sentinel_refs": invalid_sentinel_refs,
            "paired_case_ref": paired_case_ref,
        }
        return cls.model_validate(
            {**payload, "content_hash": canonical_sha256(payload)}
        )


class PressureHistorySeed(BenchmarkModel):
    seed_id: StableId
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)
    source_refs: tuple[str, ...] = Field(min_length=1)
    protected_fact_refs: tuple[StableId, ...] = ()


PressureContextCarrier = Literal[
    "stable_memory",
    "working_memory",
    "long_term_memory",
    "history_memory",
    "current_request",
]


class PressureProtectedFactSeed(BenchmarkModel):
    """把抽象 fact ref 绑定到固定文本和必须出现的五层载体。"""

    fact_ref: StableId
    expected_text: str = Field(max_length=100_000)
    required_carriers: tuple[PressureContextCarrier, ...] = Field(min_length=1)
    affects_final_answer: bool

    @model_validator(mode="after")
    def _carriers_are_unique(self) -> Self:
        if self.required_carriers != tuple(dict.fromkeys(self.required_carriers)):
            raise ValueError("受保护事实的 required_carriers 不得重复。")
        if self.affects_final_answer and not self.expected_text:
            raise ValueError("影响最终回答的受保护事实必须提供固定文本。")
        return self


PressureMemoryKind = Literal[
    "user_instruction",
    "resource_summary",
    "work_note",
    "unresolved_issue",
    "fact_reference",
]
PressureMemoryValidity = Literal[
    "active",
    "stale",
    "rejected",
    "superseded",
]


class PressureMemorySeed(BenchmarkModel):
    seed_id: StableId
    kind: PressureMemoryKind
    validity: PressureMemoryValidity = "active"
    content: str = Field(min_length=1, max_length=100_000)
    source_refs: tuple[str, ...] = Field(min_length=1)
    artifact_refs: tuple[str, ...] = ()
    retention_priority: int = Field(ge=0, le=100)
    protected_fact_refs: tuple[StableId, ...] = ()
    invalid_sentinel_refs: tuple[StableId, ...] = ()


class PressureRetrievalFragmentSeed(BenchmarkModel):
    seed_id: StableId
    kind: Literal["resource_summary", "work_note"]
    content: str = Field(min_length=1, max_length=100_000)
    source_refs: tuple[str, ...] = Field(min_length=1)
    artifact_refs: tuple[str, ...] = ()
    retention_priority: int = Field(ge=0, le=100)
    protected_fact_refs: tuple[StableId, ...] = ()


class PressureNodeArtifactSeed(BenchmarkModel):
    node_id: StableId
    output: dict[str, Any]
    source_refs: tuple[str, ...] = Field(min_length=1)
    artifact_refs: tuple[str, ...] = Field(min_length=1)
    required_output_paths: tuple[str, ...] = ()
    direct_dependency: bool = False
    protected_fact_refs: tuple[StableId, ...] = ()

    @model_validator(mode="after")
    def _dependency_has_required_path(self) -> Self:
        if self.direct_dependency and not self.required_output_paths:
            raise ValueError("直接依赖节点必须声明 required_output_paths。")
        if self.required_output_paths != tuple(
            dict.fromkeys(self.required_output_paths)
        ):
            raise ValueError("required_output_paths 不得重复。")
        return self


class PressureSeed(BenchmarkModel):
    """可直接转换为逐案历史、工作记忆、检索与节点输入的密封种子。"""

    schema_: Literal["taichu.general_agent_benchmark.pressure_seed@1"] = Field(
        alias="schema",
        default="taichu.general_agent_benchmark.pressure_seed@1",
    )
    seed_id: StableId
    pressure_plan_id: StableId
    pressure_plan_hash: Sha256
    kind: PressureKind
    fixture_blob_ref: str = Field(min_length=1, max_length=512)
    fixture_blob_sha256: Sha256
    generation_seed: Sha256
    protected_fact_refs: tuple[StableId, ...] = Field(min_length=1)
    protected_facts: tuple[PressureProtectedFactSeed, ...] = Field(min_length=1)
    invalid_sentinel_refs: tuple[StableId, ...] = ()
    history_messages: tuple[PressureHistorySeed, ...] = ()
    working_memories: tuple[PressureMemorySeed, ...] = ()
    retrieval_fragments: tuple[PressureRetrievalFragmentSeed, ...] = ()
    node_artifacts: tuple[PressureNodeArtifactSeed, ...] = ()
    plan_errors: tuple[str, ...] = ()
    todos: tuple[str, ...] = ()
    author_constraints: tuple[str, ...] = ()
    current_request: str = Field(min_length=1, max_length=1_000_000)
    long_term_memories: tuple[PressureMemorySeed, ...] = ()
    content_hash: Sha256

    _blob_ref_is_safe = field_validator("fixture_blob_ref")(_validate_fixture_blob_ref)

    @model_validator(mode="after")
    def _seed_is_complete_and_sealed(self) -> Self:
        if self.long_term_memories:
            raise ValueError("压力种子不得把 Runtime 状态伪装为长期记忆。")
        fact_refs = tuple(item.fact_ref for item in self.protected_facts)
        if fact_refs != self.protected_fact_refs:
            raise ValueError(
                "PressureSeed protected_facts 必须按计划引用顺序完整绑定。"
            )
        for values, name in (
            (self.history_messages, "history_messages"),
            (self.working_memories, "working_memories"),
            (self.retrieval_fragments, "retrieval_fragments"),
            (self.node_artifacts, "node_artifacts"),
        ):
            identifiers = tuple(
                item.node_id
                if isinstance(item, PressureNodeArtifactSeed)
                else item.seed_id
                for item in values
            )
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"压力种子 {name} 标识不得重复。")
        if self.kind is PressureKind.HISTORY and not self.history_messages:
            raise ValueError("history 压力种子必须包含历史原文。")
        if self.kind is PressureKind.WORKING_MEMORY and not self.working_memories:
            raise ValueError("working_memory 压力种子必须包含工作记忆。")
        if self.kind is PressureKind.NODE_OUTPUT and not self.node_artifacts:
            raise ValueError("node_output 压力种子必须包含节点工件。")
        if self.kind is PressureKind.MULTI_SOURCE and not all(
            (
                self.history_messages,
                self.working_memories,
                self.retrieval_fragments,
                self.node_artifacts,
            )
        ):
            raise ValueError("multi_source 压力种子必须覆盖四类运行载体。")
        if self.kind in {
            PressureKind.WORKING_MEMORY,
            PressureKind.NODE_OUTPUT,
            PressureKind.MULTI_SOURCE,
            PressureKind.EQUIVALENCE_PAIR,
            PressureKind.INVALID_MEMORY,
            PressureKind.CURRENT_REQUEST,
            PressureKind.UNSAFE_TOTAL,
        } and not any(item.direct_dependency for item in self.node_artifacts):
            raise ValueError("依赖压力种子必须包含一个直接依赖节点。")
        invalid_memories = tuple(
            item for item in self.working_memories if item.validity != "active"
        )
        if self.kind is PressureKind.INVALID_MEMORY:
            observed_sentinels = tuple(
                sorted(
                    sentinel
                    for item in invalid_memories
                    for sentinel in item.invalid_sentinel_refs
                )
            )
            if observed_sentinels != self.invalid_sentinel_refs:
                raise ValueError("失效记忆种子必须逐一绑定全部 invalid sentinel。")
            if {item.validity for item in invalid_memories} != {
                "stale",
                "rejected",
                "superseded",
            }:
                raise ValueError("失效记忆种子必须覆盖 stale/rejected/superseded。")
            if any(
                sentinel not in item.content
                for item in invalid_memories
                for sentinel in item.invalid_sentinel_refs
            ):
                raise ValueError("失效记忆哨兵必须真实存在于对应种子原文。")
        elif invalid_memories or self.invalid_sentinel_refs:
            raise ValueError("非 invalid_memory 种子不得携带失效记忆哨兵。")
        payload = self.model_dump(
            mode="python",
            by_alias=True,
            exclude={"content_hash"},
        )
        if self.content_hash != canonical_sha256(payload):
            raise ValueError("PressureSeed content_hash 与规范化内容不一致。")
        return self

    @classmethod
    def seal(cls, **payload: Any) -> PressureSeed:
        return cls.model_validate(
            {**payload, "content_hash": canonical_sha256(payload)}
        )


class PressureSeedGenerator:
    """从计划和固定 blob 纯函数式生成种子，不读取案例编号或墙钟时间。"""

    def generate(
        self,
        plan: PressurePlan,
        fixture_blob: PressureFixtureBlob,
    ) -> PressureSeed:
        if fixture_blob.blob_ref != plan.fixture_blob_ref:
            raise ValueError("PressurePlan 引用的 fixture blob 与传入内容不一致。")
        generation_seed = canonical_sha256(
            {
                "pressure_plan_hash": plan.content_hash,
                "fixture_blob_sha256": fixture_blob.content_sha256,
            }
        )
        include_history = plan.kind in {
            PressureKind.HISTORY,
            PressureKind.MULTI_SOURCE,
            PressureKind.EQUIVALENCE_PAIR,
            PressureKind.INVALID_MEMORY,
        }
        include_working = plan.kind in {
            PressureKind.WORKING_MEMORY,
            PressureKind.MULTI_SOURCE,
            PressureKind.EQUIVALENCE_PAIR,
            PressureKind.INVALID_MEMORY,
        }
        include_retrieval = plan.kind in {
            PressureKind.MULTI_SOURCE,
            PressureKind.EQUIVALENCE_PAIR,
            PressureKind.INVALID_MEMORY,
        }
        include_nodes = plan.kind in {
            PressureKind.WORKING_MEMORY,
            PressureKind.NODE_OUTPUT,
            PressureKind.MULTI_SOURCE,
            PressureKind.EQUIVALENCE_PAIR,
            PressureKind.INVALID_MEMORY,
            PressureKind.CURRENT_REQUEST,
            PressureKind.UNSAFE_TOTAL,
        }

        history_messages = self._history(plan, fixture_blob) if include_history else ()
        working_memories = (
            self._working_memories(plan, fixture_blob) if include_working else ()
        )
        retrieval_fragments = (
            self._retrieval_fragments(plan, fixture_blob) if include_retrieval else ()
        )
        node_artifacts = (
            self._node_artifacts(plan, fixture_blob) if include_nodes else ()
        )
        current_request = self._current_request(plan, fixture_blob)
        protected_facts = self._protected_facts(
            plan,
            fixture_blob=fixture_blob,
            current_request=current_request,
        )
        payload = {
            "schema": "taichu.general_agent_benchmark.pressure_seed@1",
            "seed_id": f"pressure_seed_{generation_seed[:16]}",
            "pressure_plan_id": plan.plan_id,
            "pressure_plan_hash": plan.content_hash,
            "kind": plan.kind,
            "fixture_blob_ref": fixture_blob.blob_ref,
            "fixture_blob_sha256": fixture_blob.content_sha256,
            "generation_seed": generation_seed,
            "protected_fact_refs": plan.protected_fact_refs,
            "protected_facts": protected_facts,
            "invalid_sentinel_refs": plan.invalid_sentinel_refs,
            "history_messages": history_messages,
            "working_memories": working_memories,
            "retrieval_fragments": retrieval_fragments,
            "node_artifacts": node_artifacts,
            "plan_errors": tuple(
                self._sized_content(
                    f"可恢复错误 {index + 1}：",
                    "中性错误过程填充。",
                    min(plan.unit_size, 500),
                )
                for index in range(min(4, plan.repetition_count))
            ),
            "todos": (
                "未决问题：确认青白色灯火是否贯穿当前场景。",
                "当前待办：消费直接依赖后再形成结论。",
            ),
            "author_constraints": self._author_constraints(
                plan,
                fixture_blob,
            ),
            "current_request": current_request,
            "long_term_memories": (),
        }
        return PressureSeed.seal(**payload)

    def _history(
        self,
        plan: PressurePlan,
        fixture_blob: PressureFixtureBlob,
    ) -> tuple[PressureHistorySeed, ...]:
        rows: list[PressureHistorySeed] = []
        for index in range(plan.repetition_count):
            if index == 0:
                prefix = f"早期有效作者约束：{fixture_blob.content}"
                protected = plan.protected_fact_refs
            elif index == plan.repetition_count - 1:
                prefix = "近期原始消息：继续沿用此前已经确认的约束。"
                protected = ()
            else:
                prefix = f"历史压力单元 {index + 1}："
                protected = ()
            rows.append(
                PressureHistorySeed(
                    seed_id=f"pressure_history_{index + 1:04d}",
                    role="user" if index % 2 == 0 else "assistant",
                    content=self._sized_content(
                        prefix,
                        "中性历史压力填充。",
                        plan.unit_size,
                    ),
                    source_refs=(
                        f"fixture:{fixture_blob.blob_ref}#history-{index + 1}",
                    ),
                    protected_fact_refs=protected,
                )
            )
        return tuple(rows)

    def _working_memories(
        self,
        plan: PressurePlan,
        fixture_blob: PressureFixtureBlob,
    ) -> tuple[PressureMemorySeed, ...]:
        protected_ref = self._primary_fact_ref(plan)
        rows: list[PressureMemorySeed] = [
            PressureMemorySeed(
                seed_id="pressure_instruction_0001",
                kind="user_instruction",
                content=self._sized_content(
                    f"当前有效作者指令：{fixture_blob.content}",
                    fixture_blob.content,
                    plan.unit_size,
                ),
                source_refs=("pressure:working:instruction",),
                artifact_refs=(),
                retention_priority=100,
                protected_fact_refs=(protected_ref,),
            ),
            PressureMemorySeed(
                seed_id="pressure_issue_0001",
                kind="unresolved_issue",
                content=self._sized_content(
                    "未决问题：必须在形成结论前核对直接依赖。",
                    "中性未决问题填充。",
                    plan.unit_size,
                ),
                source_refs=("pressure:working:unresolved",),
                artifact_refs=(),
                retention_priority=100,
                protected_fact_refs=(protected_ref,),
            ),
        ]
        for index in range(plan.repetition_count):
            rows.append(
                PressureMemorySeed(
                    seed_id=f"pressure_work_note_{index + 1:04d}",
                    kind="work_note",
                    content=self._sized_content(
                        f"低优先级过程笔记 {index + 1}：",
                        "中性低优先级过程填充。",
                        plan.unit_size,
                    ),
                    source_refs=(f"pressure:working:note:{index + 1}",),
                    artifact_refs=(f"artifact:pressure:note:{index + 1}",),
                    retention_priority=0,
                )
            )
        if plan.kind is PressureKind.INVALID_MEMORY:
            validity_cycle: tuple[PressureMemoryValidity, ...] = (
                "stale",
                "rejected",
                "superseded",
            )
            for index, sentinel_ref in enumerate(plan.invalid_sentinel_refs):
                rows.append(
                    PressureMemorySeed(
                        seed_id=f"pressure_invalid_{index + 1:04d}",
                        kind="work_note",
                        validity=validity_cycle[index % len(validity_cycle)],
                        content=self._sized_content(
                            f"无效记忆哨兵 {sentinel_ref}：",
                            "中性无效记忆填充。",
                            plan.unit_size,
                        ),
                        source_refs=(f"pressure:invalid:{index + 1}",),
                        artifact_refs=(),
                        retention_priority=100,
                        invalid_sentinel_refs=(sentinel_ref,),
                    )
                )
        return tuple(rows)

    def _retrieval_fragments(
        self,
        plan: PressurePlan,
        fixture_blob: PressureFixtureBlob,
    ) -> tuple[PressureRetrievalFragmentSeed, ...]:
        return tuple(
            PressureRetrievalFragmentSeed(
                seed_id=f"pressure_retrieval_{index + 1:04d}",
                kind="resource_summary",
                content=self._sized_content(
                    f"可重建检索片段 {index + 1}：",
                    "中性检索压力填充。",
                    plan.unit_size,
                ),
                source_refs=(f"manuscript:pressure:{index + 1}",),
                artifact_refs=(f"retrieval:pressure:{index + 1}",),
                retention_priority=10,
            )
            for index in range(max(1, min(plan.repetition_count, 12)))
        )

    def _node_artifacts(
        self,
        plan: PressurePlan,
        fixture_blob: PressureFixtureBlob,
    ) -> tuple[PressureNodeArtifactSeed, ...]:
        primary_ref = self._primary_fact_ref(plan)
        item_count = (
            plan.repetition_count
            if plan.kind is PressureKind.NODE_OUTPUT
            else max(6, min(plan.repetition_count, 24))
        )
        items = [
            {
                "item_id": f"structure_{index + 1:04d}",
                "fact_ref": primary_ref,
                "summary": self._sized_content(
                    f"结构条目 {index + 1}：",
                    fixture_blob.content,
                    plan.unit_size,
                ),
            }
            for index in range(item_count)
        ]
        direct = PressureNodeArtifactSeed(
            node_id="pressure_source",
            output={
                "items": items,
                "total": item_count,
                "returned_count": item_count,
                "contract": "structure_items",
            },
            source_refs=("structure:pressure:root",),
            artifact_refs=("artifact:pressure:structure",),
            required_output_paths=("items",),
            direct_dependency=True,
            protected_fact_refs=(primary_ref,),
        )
        if plan.kind is PressureKind.NODE_OUTPUT:
            return (direct,)
        incidental = tuple(
            PressureNodeArtifactSeed(
                node_id=f"pressure_side_{index + 1:02d}",
                output={
                    "content": self._sized_content(
                        f"旁支节点结果 {index + 1}：",
                        "中性旁支节点填充。",
                        plan.unit_size,
                    ),
                    "ordinal": index + 1,
                },
                source_refs=(f"manuscript:pressure:side:{index + 1}",),
                artifact_refs=(f"artifact:pressure:side:{index + 1}",),
            )
            for index in range(min(3, plan.repetition_count))
        )
        return (*incidental, direct)

    def _protected_facts(
        self,
        plan: PressurePlan,
        *,
        fixture_blob: PressureFixtureBlob,
        current_request: str,
    ) -> tuple[PressureProtectedFactSeed, ...]:
        fact_carriers_by_kind: dict[
            PressureKind,
            tuple[PressureContextCarrier, ...],
        ] = {
            PressureKind.HISTORY: (
                "history_memory",
                "current_request",
            ),
            PressureKind.WORKING_MEMORY: ("working_memory",),
            PressureKind.NODE_OUTPUT: ("working_memory",),
            PressureKind.MULTI_SOURCE: (
                "history_memory",
                "working_memory",
                "current_request",
            ),
            PressureKind.EQUIVALENCE_PAIR: (
                "history_memory",
                "working_memory",
                "current_request",
            ),
            PressureKind.INVALID_MEMORY: ("working_memory",),
            PressureKind.CURRENT_REQUEST: ("current_request",),
            PressureKind.UNSAFE_TOTAL: ("current_request",),
        }
        fact_carriers = fact_carriers_by_kind[plan.kind]
        rows: list[PressureProtectedFactSeed] = []
        for fact_ref in plan.protected_fact_refs:
            if fact_ref == "current_request":
                expected_text = current_request
                required_carriers: tuple[PressureContextCarrier, ...] = (
                    "current_request",
                )
                affects_final_answer = False
            elif fact_ref == "stable_rules":
                expected_text = ""
                required_carriers = ("stable_memory",)
                affects_final_answer = False
            else:
                expected_text = fixture_blob.content
                required_carriers = fact_carriers
                affects_final_answer = True
            rows.append(
                PressureProtectedFactSeed(
                    fact_ref=fact_ref,
                    expected_text=expected_text,
                    required_carriers=required_carriers,
                    affects_final_answer=affects_final_answer,
                )
            )
        return tuple(rows)

    def _current_request(
        self,
        plan: PressurePlan,
        fixture_blob: PressureFixtureBlob,
    ) -> str:
        if plan.kind in {PressureKind.CURRENT_REQUEST, PressureKind.UNSAFE_TOTAL}:
            target_size = plan.repetition_count * plan.unit_size
            if plan.kind is PressureKind.UNSAFE_TOTAL:
                target_size = min(target_size, 90_000)
            return self._sized_content(
                "  请逐字保留当前请求的空白与顺序。\n",
                fixture_blob.content,
                target_size,
            )
        return "请依据仍然有效的上下文完成当前任务，并明确说明所用事实来源。"

    @staticmethod
    def _author_constraints(
        plan: PressurePlan,
        fixture_blob: PressureFixtureBlob,
    ) -> tuple[str, ...]:
        if plan.kind in {
            PressureKind.HISTORY,
            PressureKind.MULTI_SOURCE,
            PressureKind.EQUIVALENCE_PAIR,
        }:
            return (fixture_blob.content,)
        if plan.kind in {
            PressureKind.CURRENT_REQUEST,
            PressureKind.UNSAFE_TOTAL,
        }:
            constraints = [
                "保持当前请求的首尾空格与换行。",
                "保持关键约束的原始顺序。",
            ]
            if plan.kind is PressureKind.UNSAFE_TOTAL:
                overflow = max(
                    0,
                    plan.repetition_count * plan.unit_size - 90_000,
                )
                if overflow:
                    constraints.append(
                        PressureSeedGenerator._sized_content(
                            "不可静默删除的当前关键约束：",
                            fixture_blob.content,
                            overflow,
                        )
                    )
            return tuple(constraints)
        return ()

    @staticmethod
    def _primary_fact_ref(plan: PressurePlan) -> str:
        return next(
            (
                item
                for item in plan.protected_fact_refs
                if item not in {"current_request", "stable_rules"}
            ),
            plan.protected_fact_refs[0],
        )

    @staticmethod
    def _sized_content(prefix: str, fill: str, unit_size: int) -> str:
        if unit_size <= len(prefix):
            return prefix[:unit_size]
        filler = fill or "压"
        repeat_count = (unit_size - len(prefix) + len(filler) - 1) // len(filler)
        return (prefix + filler * repeat_count)[:unit_size]


class PressureBehaviorCheck(BenchmarkModel):
    check_id: StableId
    description: str = Field(min_length=1, max_length=1_000)
    satisfied: bool
    evidence_refs: tuple[str, ...] = ()


class PressureBehaviorArtifact(BenchmarkModel):
    """固定规则从真实上下文快照派生的最终行为产物。"""

    schema_: Literal["taichu.general_agent_benchmark.pressure_behavior@1"] = Field(
        alias="schema",
        default="taichu.general_agent_benchmark.pressure_behavior@1",
    )
    pressure_plan_ref: StableId
    pressure_plan_hash: Sha256
    pressure_seed_hash: Sha256
    context_snapshot_id: str = Field(min_length=1, max_length=128)
    context_snapshot_sha256: Sha256
    context_trace_sha256: Sha256
    status: Literal["completed", "failed"]
    checks: tuple[PressureBehaviorCheck, ...] = Field(min_length=1)
    required_fact_refs: tuple[StableId, ...]
    consumed_fact_refs: tuple[StableId, ...]
    missing_fact_refs: tuple[StableId, ...]
    final_answer: str = Field(min_length=1, max_length=200_000)
    source_refs: tuple[str, ...] = ()
    content_hash: Sha256

    @model_validator(mode="after")
    def _behavior_is_consistent_and_sealed(self) -> Self:
        required = tuple(sorted(set(self.required_fact_refs)))
        consumed = tuple(sorted(set(self.consumed_fact_refs)))
        missing = tuple(sorted(set(self.missing_fact_refs)))
        if required != self.required_fact_refs:
            raise ValueError("required_fact_refs 必须排序且不得重复。")
        if consumed != self.consumed_fact_refs:
            raise ValueError("consumed_fact_refs 必须排序且不得重复。")
        if missing != self.missing_fact_refs:
            raise ValueError("missing_fact_refs 必须排序且不得重复。")
        if set(consumed) - set(required):
            raise ValueError("已消费事实必须属于 required_fact_refs。")
        if missing != tuple(sorted(set(required) - set(consumed))):
            raise ValueError("missing_fact_refs 必须由 required/consumed 唯一派生。")
        completed = not missing and all(item.satisfied for item in self.checks)
        if (self.status == "completed") != completed:
            raise ValueError("压力行为终态与固定检查结果不一致。")
        payload = self.model_dump(
            mode="python",
            by_alias=True,
            exclude={"content_hash"},
        )
        if self.content_hash != canonical_sha256(payload):
            raise ValueError("压力行为产物 content_hash 与规范化内容不一致。")
        return self

    @classmethod
    def seal(cls, **payload: Any) -> PressureBehaviorArtifact:
        return cls.model_validate(
            {**payload, "content_hash": canonical_sha256(payload)}
        )


class PressureBehaviorAssertionResult(BenchmarkModel):
    pressure_plan_ref: StableId
    status: Literal["passed", "failed"]
    behavior_artifact_hash: Sha256
    failed_check_ids: tuple[StableId, ...]
    missing_fact_refs: tuple[StableId, ...]
    final_answer_sha256: Sha256


class PressureBehaviorEvaluator:
    """用固定检查消费快照并生成答案，不调用模型裁判。"""

    def evaluate(
        self,
        *,
        plan: PressurePlan,
        seed: PressureSeed,
        snapshot: GeneralAgentContextSnapshot,
    ) -> PressureBehaviorArtifact:
        _assert_pressure_owners(plan, seed, snapshot)
        trace = snapshot.assembly_trace
        if trace is None:
            raise ValueError("压力行为判定要求非空 AssemblyTrace。")
        layers = {item.layer: item for item in trace.layers}
        checks: list[PressureBehaviorCheck] = []
        source_refs: list[str] = []

        stable = layers["stable_memory"]
        checks.append(
            PressureBehaviorCheck(
                check_id="stable_memory_protected",
                description="稳定记忆在压力前后保持完整。",
                satisfied=(
                    stable.pre_count == stable.post_count
                    and stable.pre_char_count == stable.post_char_count
                    and stable.omitted_count == 0
                ),
                evidence_refs=(trace.stable_memory_sha256,),
            )
        )
        current_ok = (
            snapshot.envelope.current_request.content == seed.current_request
            and trace.current_request_sha256
            == sha256(seed.current_request.encode("utf-8")).hexdigest()
        )
        checks.append(
            PressureBehaviorCheck(
                check_id="current_request_protected",
                description="当前请求原文及内容身份保持完整。",
                satisfied=current_ok,
                evidence_refs=(trace.current_request_sha256,),
            )
        )

        required_facts = tuple(
            item for item in seed.protected_facts if item.affects_final_answer
        )
        consumed_fact_refs: list[str] = []
        for index, fact in enumerate(required_facts, start=1):
            fact_source_refs = _protected_fact_source_refs(
                seed,
                fact.fact_ref,
            )
            present = all(
                fact.expected_text in _carrier_text(snapshot, carrier)
                for carrier in fact.required_carriers
            )
            if present:
                consumed_fact_refs.append(fact.fact_ref)
                source_refs.extend(fact_source_refs)
            checks.append(
                PressureBehaviorCheck(
                    check_id=f"protected_fact_{index:03d}",
                    description=f"受保护事实 {fact.fact_ref} 进入必要载体。",
                    satisfied=present,
                    evidence_refs=(fact.fact_ref, *fact_source_refs),
                )
            )

        if seed.history_messages:
            recent = seed.history_messages[-1]
            recent_ok = recent.content in {
                item.content for item in snapshot.envelope.history_memory.messages
            }
            checks.append(
                PressureBehaviorCheck(
                    check_id="recent_history_raw_retained",
                    description="近期历史消息仍以原文保留。",
                    satisfied=recent_ok,
                    evidence_refs=recent.source_refs,
                )
            )

        protected_memories = tuple(
            item for item in seed.working_memories if item.protected_fact_refs
        )
        if protected_memories:
            actual_memories = _session_items(snapshot)
            protected_memory_ok = all(
                any(actual["content"] == expected.content for actual in actual_memories)
                for expected in protected_memories
            )
            checks.append(
                PressureBehaviorCheck(
                    check_id="protected_working_memory_retained",
                    description="当前指令与未决问题仍保留在工作记忆。",
                    satisfied=protected_memory_ok,
                    evidence_refs=tuple(
                        ref for item in protected_memories for ref in item.source_refs
                    ),
                )
            )
            source_refs.extend(
                ref for item in protected_memories for ref in item.source_refs
            )

        direct_nodes = tuple(
            item for item in seed.node_artifacts if item.direct_dependency
        )
        if direct_nodes:
            direct_ok = all(
                _direct_node_contract_preserved(snapshot, item) for item in direct_nodes
            )
            checks.append(
                PressureBehaviorCheck(
                    check_id="direct_dependency_retained",
                    description="直接依赖、required paths 与来源合同仍可消费。",
                    satisfied=direct_ok,
                    evidence_refs=tuple(
                        ref
                        for item in direct_nodes
                        for ref in (*item.source_refs, *item.artifact_refs)
                    ),
                )
            )
            source_refs.extend(
                ref
                for item in direct_nodes
                for ref in (*item.source_refs, *item.artifact_refs)
            )

            count_ok = all(
                _projected_required_count(snapshot, item) == _required_item_count(item)
                for item in direct_nodes
            )
            checks.append(
                PressureBehaviorCheck(
                    check_id="required_output_count_retained",
                    description="required output path 保留完整条目计数。",
                    satisfied=count_ok,
                    evidence_refs=tuple(item.node_id for item in direct_nodes),
                )
            )
            if seed.kind in {
                PressureKind.EQUIVALENCE_PAIR,
                PressureKind.CURRENT_REQUEST,
            }:
                checks.append(
                    PressureBehaviorCheck(
                        check_id="necessary_execution_chain_completed",
                        description=(
                            "必要能力链已消费直接依赖及 required output contract。"
                        ),
                        satisfied=_necessary_execution_chain_completed(
                            snapshot,
                            direct_nodes,
                        ),
                        evidence_refs=tuple(item.node_id for item in direct_nodes),
                    )
                )

        omission_ok = _omission_priority_is_valid(seed, snapshot)
        checks.append(
            PressureBehaviorCheck(
                check_id="omission_priority_respected",
                description="受保护输入没有被逐层删除，结果可按完整引用回读。",
                satisfied=omission_ok,
                evidence_refs=tuple(trace.omitted_item_refs),
            )
        )

        required_fact_refs = tuple(sorted(item.fact_ref for item in required_facts))
        consumed_refs = tuple(sorted(set(consumed_fact_refs)))
        missing_refs = tuple(sorted(set(required_fact_refs) - set(consumed_refs)))
        completed = not missing_refs and all(item.satisfied for item in checks)
        answer_parts = [
            f"已采用受保护事实：{fact.expected_text}"
            for fact in required_facts
            if fact.fact_ref in consumed_refs
        ]
        if current_ok:
            answer_parts.append("当前请求原文保持完整。")
        if seed.history_messages and any(
            item.check_id == "recent_history_raw_retained" and item.satisfied
            for item in checks
        ):
            answer_parts.append("近期原始消息保持完整。")
        if protected_memories and any(
            item.check_id == "protected_working_memory_retained" and item.satisfied
            for item in checks
        ):
            answer_parts.append("当前指令与未决问题仍参与结论。")
        if direct_nodes and any(
            item.check_id == "required_output_count_retained" and item.satisfied
            for item in checks
        ):
            counts = tuple(_required_item_count(item) for item in direct_nodes)
            answer_parts.append(
                "已按 required output path 消费完整计数 "
                + "、".join(str(item) for item in counts)
                + "；未展示条目不代表不存在。"
            )
        if completed:
            answer_parts.append("压力目标已完成。")
        else:
            failed = [item.check_id for item in checks if not item.satisfied]
            answer_parts.append(
                "压力目标未完成：缺少 " + "、".join([*missing_refs, *failed]) + "。"
            )
        payload = {
            "schema": "taichu.general_agent_benchmark.pressure_behavior@1",
            "pressure_plan_ref": plan.plan_id,
            "pressure_plan_hash": plan.content_hash,
            "pressure_seed_hash": seed.content_hash,
            "context_snapshot_id": snapshot.snapshot_id,
            "context_snapshot_sha256": snapshot.content_sha256,
            "context_trace_sha256": trace.trace_sha256,
            "status": "completed" if completed else "failed",
            "checks": tuple(checks),
            "required_fact_refs": required_fact_refs,
            "consumed_fact_refs": consumed_refs,
            "missing_fact_refs": missing_refs,
            "final_answer": "".join(answer_parts),
            "source_refs": tuple(dict.fromkeys(source_refs)),
        }
        return PressureBehaviorArtifact.seal(**payload)


class PressureBehaviorOracle:
    """只按密封行为产物字段判断，不解释自然语言。"""

    def evaluate(
        self,
        artifact: PressureBehaviorArtifact,
    ) -> PressureBehaviorAssertionResult:
        failed = tuple(item.check_id for item in artifact.checks if not item.satisfied)
        passed = (
            artifact.status == "completed"
            and not artifact.missing_fact_refs
            and not failed
        )
        return PressureBehaviorAssertionResult(
            pressure_plan_ref=artifact.pressure_plan_ref,
            status="passed" if passed else "failed",
            behavior_artifact_hash=artifact.content_hash,
            failed_check_ids=failed,
            missing_fact_refs=artifact.missing_fact_refs,
            final_answer_sha256=canonical_sha256(artifact.final_answer),
        )


class PressureContextPreservationProjector:
    """把 AssemblyTrace 与受保护内容投影成 Typed Oracle 的固定观察。"""

    def project(
        self,
        *,
        plan: PressurePlan,
        seed: PressureSeed,
        snapshot: GeneralAgentContextSnapshot,
    ) -> ContextPreservationObservation:
        from taichu.application.evaluations.general_agent_benchmark.oracles import (
            ContextCarrierObservation,
            ContextPreservationObservation,
        )

        _assert_pressure_owners(plan, seed, snapshot)
        trace = snapshot.assembly_trace
        if trace is None:
            raise ValueError("上下文保护投影要求非空 AssemblyTrace。")
        layers = {item.layer: item for item in trace.layers}
        before_after: dict[
            PressureContextCarrier,
            tuple[Any, Any, tuple[str, ...]],
        ] = {}

        stable_layer = layers["stable_memory"]
        stable_after: Any = {"stable_memory_sha256": trace.stable_memory_sha256}
        if (
            stable_layer.pre_count != stable_layer.post_count
            or stable_layer.pre_char_count != stable_layer.post_char_count
            or stable_layer.omitted_count
        ):
            stable_after = {
                "post_count": stable_layer.post_count,
                "post_chars": stable_layer.post_char_count,
            }
        before_after["stable_memory"] = (
            {"stable_memory_sha256": trace.stable_memory_sha256},
            stable_after,
            tuple(
                item.fact_ref
                for item in seed.protected_facts
                if "stable_memory" in item.required_carriers
            )
            or ("stable_rules",),
        )

        expected_working = _expected_working_projection(seed)
        actual_working = _actual_working_projection(seed, snapshot)
        before_after["working_memory"] = (
            expected_working,
            actual_working,
            _working_protected_refs(seed),
        )
        before_after["long_term_memory"] = (
            (),
            tuple(
                item.model_dump(mode="json")
                for item in snapshot.envelope.long_term_memory
            ),
            (),
        )
        before_after["history_memory"] = (
            _expected_history_projection(seed),
            _actual_history_projection(seed, snapshot),
            _history_protected_refs(seed),
        )
        expected_current = {
            "content": seed.current_request,
            "constraints": seed.author_constraints,
        }
        actual_current = {
            "content": snapshot.envelope.current_request.content,
            "constraints": tuple(snapshot.envelope.current_request.user_constraints),
        }
        before_after["current_request"] = (
            expected_current,
            actual_current,
            tuple(
                item.fact_ref
                for item in seed.protected_facts
                if "current_request" in item.required_carriers
            )
            or ("current_request",),
        )

        carriers = []
        for carrier in (
            "stable_memory",
            "working_memory",
            "long_term_memory",
            "history_memory",
            "current_request",
        ):
            expected, actual, protected_refs = before_after[carrier]
            before_sha256 = canonical_sha256(expected)
            after_sha256 = canonical_sha256(actual)
            carriers.append(
                ContextCarrierObservation(
                    carrier=carrier,
                    before_sha256=before_sha256,
                    after_sha256=after_sha256,
                    preserved=before_sha256 == after_sha256,
                    protected_refs=tuple(dict.fromkeys(protected_refs)),
                )
            )
        return ContextPreservationObservation(
            pressure_plan_ref=plan.plan_id,
            carriers=tuple(carriers),
            current_request_before_sha256=canonical_sha256(seed.current_request),
            current_request_after_sha256=canonical_sha256(
                snapshot.envelope.current_request.content
            ),
        )


class PressureResultContractProjector:
    """把正常版与压力版投影为可机械比较的语义结果合同。"""

    def project_result(
        self,
        *,
        seed: PressureSeed,
        snapshot: GeneralAgentContextSnapshot,
        behavior: PressureBehaviorArtifact,
        execution_plan: GeneralAgentExecutionPlan,
        resource_before: Any,
        resource_after: Any,
    ) -> ResultContractProjection:
        from taichu.application.evaluations.general_agent_benchmark.oracles import (
            ResultContractProjection,
        )

        _assert_behavior_snapshot(seed, snapshot, behavior)
        checks = {item.check_id: item.satisfied for item in behavior.checks}
        protected_fact_refs = set(behavior.consumed_fact_refs)
        for fact in seed.protected_facts:
            if (
                fact.fact_ref == "current_request"
                and checks.get("current_request_protected") is True
            ):
                protected_fact_refs.add(fact.fact_ref)
            elif (
                fact.fact_ref == "stable_rules"
                and checks.get("stable_memory_protected") is True
            ):
                protected_fact_refs.add(fact.fact_ref)

        necessary_node_ids = _necessary_plan_node_ids(seed, execution_plan)
        nodes_by_id = {item.node_id: item for item in execution_plan.nodes}
        capability_names = {
            nodes_by_id[node_id].capability_name
            for node_id in necessary_node_ids
            if node_id in nodes_by_id
        }
        topology_edges = {
            (
                f"{nodes_by_id[dependency].capability_name}>"
                f"{nodes_by_id[node.node_id].capability_name}"
            )
            for node in execution_plan.nodes
            if node.node_id in necessary_node_ids
            for dependency in node.dependencies
            if dependency in necessary_node_ids and dependency in nodes_by_id
        }

        artifact_contracts = {"final_answer"}
        for direct in (item for item in seed.node_artifacts if item.direct_dependency):
            if not _direct_node_contract_preserved(snapshot, direct):
                continue
            contract = direct.output.get("contract")
            if isinstance(contract, str) and re.fullmatch(
                r"[a-z][a-z0-9_]{2,63}", contract
            ):
                artifact_contracts.add(contract)

        claim_ids = set(behavior.consumed_fact_refs)
        if behavior.status == "completed":
            claim_ids.add("pressure_goal_completed")
        return ResultContractProjection(
            claim_ids=tuple(sorted(claim_ids)),
            capability_names=tuple(sorted(capability_names)),
            topology_edges=tuple(sorted(topology_edges)),
            protected_fact_refs=tuple(sorted(protected_fact_refs)),
            artifact_contracts=tuple(sorted(artifact_contracts)),
            resource_diff_sha256=canonical_sha256(
                {
                    "before": resource_before,
                    "after": resource_after,
                }
            ),
        )

    def compare(
        self,
        *,
        plan: PressurePlan,
        baseline: ResultContractProjection,
        candidate: ResultContractProjection,
    ) -> ResultContractEquivalenceObservation:
        from taichu.application.evaluations.general_agent_benchmark.oracles import (
            ResultContractEquivalenceObservation,
        )

        if plan.kind is not PressureKind.EQUIVALENCE_PAIR:
            raise ValueError("结果合同成对比较只接受 equivalence_pair 压力计划。")
        return ResultContractEquivalenceObservation(
            pressure_plan_ref=plan.plan_id,
            baseline=baseline,
            candidate=candidate,
        )


PressureMemoryScanCarrier = Literal[
    "basis",
    "repair",
    "digest",
    "fallback",
    "history",
    "working_memory",
    "node",
    "subagent",
    "final",
]
PressureInvalidMemoryState = Literal["stale", "rejected", "superseded"]
_PRESSURE_MEMORY_SCAN_CARRIERS: tuple[PressureMemoryScanCarrier, ...] = (
    "basis",
    "repair",
    "digest",
    "fallback",
    "history",
    "working_memory",
    "node",
    "subagent",
    "final",
)


class PressureMemoryIsolationProjector:
    """扫描正常摘要、降级摘要和最终结果中的失效记忆哨兵。"""

    def project(
        self,
        *,
        plan: PressurePlan,
        seed: PressureSeed,
        snapshot: GeneralAgentContextSnapshot,
        fallback_snapshot: GeneralAgentContextSnapshot,
        behavior: PressureBehaviorArtifact,
        carrier_overrides: Mapping[str, Any] | None = None,
        memory_seed_ref: StableId | None = None,
    ) -> tuple[MemoryCarrierObservation, ...]:
        from taichu.application.evaluations.general_agent_benchmark.oracles import (
            MemoryCarrierObservation,
        )

        if plan.kind is not PressureKind.INVALID_MEMORY:
            raise ValueError("失效记忆扫描只接受 invalid_memory 压力计划。")
        _assert_pressure_owners(plan, seed, snapshot)
        _assert_pressure_owners(plan, seed, fallback_snapshot)
        _assert_behavior_snapshot(seed, snapshot, behavior)
        overrides = dict(carrier_overrides or {})
        unknown = set(overrides) - set(_PRESSURE_MEMORY_SCAN_CARRIERS)
        if unknown:
            raise ValueError(
                "失效记忆扫描包含未知 carrier：" + "、".join(sorted(unknown))
            )

        normal_working = snapshot.envelope.working_memory
        fallback_working = fallback_snapshot.envelope.working_memory
        payloads: dict[PressureMemoryScanCarrier, Any] = {
            "basis": {
                "normal": [
                    item.model_dump(mode="json") for item in normal_working.memories
                ],
                "fallback": [
                    item.model_dump(mode="json") for item in fallback_working.memories
                ],
            },
            "repair": {
                "normal": [
                    item.model_dump(mode="json")
                    for item in normal_working.invalidated_memories
                ],
                "fallback": [
                    item.model_dump(mode="json")
                    for item in fallback_working.invalidated_memories
                ],
            },
            "digest": (
                normal_working.session_memory.model_dump(mode="json")
                if normal_working.session_memory is not None
                else None
            ),
            "fallback": {
                "fallback_used": fallback_snapshot.envelope.fallback_used,
                "digest": (
                    fallback_working.session_memory.model_dump(mode="json")
                    if fallback_working.session_memory is not None
                    else None
                ),
            },
            "history": {
                "normal": snapshot.envelope.history_memory.model_dump(mode="json"),
                "fallback": fallback_snapshot.envelope.history_memory.model_dump(
                    mode="json"
                ),
            },
            "working_memory": {
                "normal": normal_working.model_dump(mode="json"),
                "fallback": fallback_working.model_dump(mode="json"),
            },
            "node": {
                "normal": normal_working.node_summaries,
                "fallback": fallback_working.node_summaries,
            },
            "subagent": {
                "status": behavior.status,
                "consumed_fact_refs": behavior.consumed_fact_refs,
                "source_refs": behavior.source_refs,
            },
            "final": behavior.final_answer,
        }
        payloads.update(
            {
                carrier: value
                for carrier, value in overrides.items()
                if carrier in _PRESSURE_MEMORY_SCAN_CARRIERS
            }
        )
        invalid_memories = tuple(
            item for item in seed.working_memories if item.validity != "active"
        )
        if {item.validity for item in invalid_memories} != {
            "stale",
            "rejected",
            "superseded",
        }:
            raise ValueError("失效记忆压力种子必须完整覆盖 stale/rejected/superseded。")
        observations: list[MemoryCarrierObservation] = []
        for memory in invalid_memories:
            state = _pressure_invalid_memory_state(memory.validity)
            for sentinel_ref in memory.invalid_sentinel_refs:
                for carrier in _PRESSURE_MEMORY_SCAN_CARRIERS:
                    text = json.dumps(
                        payloads[carrier],
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    observations.append(
                        MemoryCarrierObservation(
                            memory_seed_ref=memory_seed_ref or plan.plan_id,
                            state=state,
                            carrier=carrier,
                            sentinel_ref=sentinel_ref,
                            occurrence_count=text.count(sentinel_ref),
                        )
                    )
        return tuple(observations)


class PressureUnsafeRefusalArtifact(BenchmarkModel):
    """在模型规划与能力调用前形成的密封 fail-closed 证据。"""

    schema_: Literal["taichu.general_agent_benchmark.pressure_unsafe_refusal@2"] = (
        Field(
            alias="schema",
            default="taichu.general_agent_benchmark.pressure_unsafe_refusal@2",
        )
    )
    pressure_plan_ref: StableId
    pressure_plan_hash: Sha256
    pressure_seed_hash: Sha256
    phase: Literal["before_planning"] = "before_planning"
    run_status: Literal["safe_failure"] = "safe_failure"
    resumable: Literal[False] = False
    recovery_action: Literal["stop"] = "stop"
    reason_code: Literal["unsafe_context"] = "unsafe_context"
    message: str = Field(min_length=1, max_length=2_000)
    context_window_tokens: int = Field(ge=0)
    input_tokens: int = Field(gt=0)
    current_request_char_count: int = Field(gt=0)
    current_request_byte_sha256: Sha256
    current_request_canonical_sha256: Sha256
    stable_memory_sha256: Sha256
    capability_call_count: Literal[0] = 0
    capability_result_count: Literal[0] = 0
    effect_count: Literal[0] = 0
    content_hash: Sha256

    @model_validator(mode="after")
    def _refusal_is_sealed(self) -> Self:
        if self.input_tokens <= self.context_window_tokens:
            raise ValueError("安全拒绝证据要求必要输入超过模型窗口。")
        payload = self.model_dump(
            mode="python",
            by_alias=True,
            exclude={"content_hash"},
        )
        if self.content_hash != canonical_sha256(payload):
            raise ValueError("安全拒绝证据 content_hash 不匹配。")
        return self

    @classmethod
    def from_error(
        cls,
        *,
        plan: PressurePlan,
        seed: PressureSeed,
        error: ContextCapacityError,
    ) -> PressureUnsafeRefusalArtifact:
        from taichu.application.general_agent.pipeline import ContextCapacityError

        if plan.kind is not PressureKind.UNSAFE_TOTAL:
            raise ValueError("安全拒绝证据只接受 unsafe_total 压力计划。")
        if (
            seed.pressure_plan_id != plan.plan_id
            or seed.pressure_plan_hash != plan.content_hash
            or seed.content_hash is None
        ):
            raise ValueError("安全拒绝种子与 PressurePlan 身份不一致。")
        if not isinstance(error, ContextCapacityError):
            raise TypeError("安全拒绝证据必须来自 ContextCapacityError。")
        current_request_sha256 = sha256(
            seed.current_request.encode("utf-8")
        ).hexdigest()
        if (
            error.reason_code != "unsafe_context"
            or error.context_window_tokens is None
            or error.input_tokens is None
            or error.current_request_sha256 != current_request_sha256
            or error.stable_memory_sha256 is None
        ):
            raise ValueError("ContextCapacityError 缺少可核验的不安全上下文证据。")
        payload = {
            "schema": ("taichu.general_agent_benchmark.pressure_unsafe_refusal@2"),
            "pressure_plan_ref": plan.plan_id,
            "pressure_plan_hash": plan.content_hash,
            "pressure_seed_hash": seed.content_hash,
            "phase": "before_planning",
            "run_status": "safe_failure",
            "resumable": False,
            "recovery_action": "stop",
            "reason_code": "unsafe_context",
            "message": str(error),
            "context_window_tokens": error.context_window_tokens,
            "input_tokens": error.input_tokens,
            "current_request_char_count": len(seed.current_request),
            "current_request_byte_sha256": current_request_sha256,
            "current_request_canonical_sha256": canonical_sha256(seed.current_request),
            "stable_memory_sha256": error.stable_memory_sha256,
            "capability_call_count": 0,
            "capability_result_count": 0,
            "effect_count": 0,
        }
        return cls.model_validate(
            {
                **payload,
                "content_hash": canonical_sha256(payload),
            }
        )


class PressureUnsafeRefusalProjector:
    """把 fail-closed 证据投影为现有 Typed Oracle 可消费的观察。"""

    def project_context(
        self,
        *,
        plan: PressurePlan,
        seed: PressureSeed,
        artifact: PressureUnsafeRefusalArtifact,
    ) -> ContextPreservationObservation:
        from taichu.application.evaluations.general_agent_benchmark.oracles import (
            ContextCarrierObservation,
            ContextPreservationObservation,
        )

        _assert_refusal_owners(plan, seed, artifact)
        not_assembled = canonical_sha256({"state": "not_assembled_before_planning"})
        values: tuple[
            tuple[PressureContextCarrier, Sha256, tuple[StableId, ...]],
            ...,
        ] = (
            (
                "stable_memory",
                artifact.stable_memory_sha256,
                ("stable_rules",),
            ),
            ("working_memory", not_assembled, ()),
            ("long_term_memory", not_assembled, ()),
            ("history_memory", not_assembled, ()),
            (
                "current_request",
                artifact.current_request_canonical_sha256,
                ("current_request",),
            ),
        )
        return ContextPreservationObservation(
            pressure_plan_ref=plan.plan_id,
            carriers=tuple(
                ContextCarrierObservation(
                    carrier=carrier,
                    before_sha256=value_sha256,
                    after_sha256=value_sha256,
                    preserved=True,
                    protected_refs=protected_refs,
                )
                for carrier, value_sha256, protected_refs in values
            ),
            current_request_before_sha256=(artifact.current_request_canonical_sha256),
            current_request_after_sha256=(artifact.current_request_canonical_sha256),
        )

    def project_recovery_decision(
        self,
        artifact: PressureUnsafeRefusalArtifact,
    ) -> ObservedRecoveryDecision:
        from taichu.application.evaluations.general_agent_benchmark.observations import (
            ObservedRecoveryDecision,
        )

        return ObservedRecoveryDecision(
            decision_id=f"pressure_refusal:{artifact.pressure_plan_ref}",
            action=artifact.recovery_action,
            reason_code=artifact.reason_code,
            evidence_sha256=artifact.content_hash,
        )

    def project_observation(
        self,
        *,
        plan: PressurePlan,
        seed: PressureSeed,
        artifact: PressureUnsafeRefusalArtifact,
        owner: EvidenceOwner,
        resource_state: dict[str, Any],
    ) -> CaseObservation:
        from taichu.application.evaluations.general_agent_benchmark.observations import (
            CaseObservation,
            EvidenceIntegrityStatus,
            ObservedBudgetUsage,
            ObservedFinalAnswer,
            ObservedResourceSnapshot,
            ObservedTerminalState,
        )

        _assert_refusal_owners(plan, seed, artifact)
        if owner.case_id != plan.plan_id:
            raise ValueError("安全拒绝观察 owner.case_id 与压力计划不一致。")
        before = ObservedResourceSnapshot(
            snapshot_ref="pressure_resources",
            phase="before",
            content_sha256=canonical_sha256(resource_state),
            payload=resource_state,
        )
        after = before.model_copy(update={"phase": "after"})
        payload = {
            "owner": owner,
            "user_request_raw": seed.current_request,
            "user_request_sha256": canonical_sha256(seed.current_request),
            "plan": None,
            "plan_sha256": None,
            "nodes": (),
            "invocations": (),
            "final_answer": ObservedFinalAnswer.create(
                text=artifact.message,
                source_refs=(f"pressure:{plan.plan_id}", artifact.content_hash),
            ),
            "artifacts": (),
            "resource_snapshots": (before, after),
            "capability_result_refs": (),
            "effect_refs": (),
            "checkpoint_refs": (),
            "context_snapshot_refs": (),
            "recovery_decisions": (self.project_recovery_decision(artifact),),
            "terminal": ObservedTerminalState(
                run_status=artifact.run_status,
                stop_reason=artifact.reason_code,
                resumable=artifact.resumable,
                pending_human_kind=None,
            ),
            "budget": ObservedBudgetUsage(
                node_executions=0,
                capability_calls=artifact.capability_call_count,
                model_calls=0,
                total_tokens=0,
                runtime_ms=0,
                context_tokens=artifact.input_tokens,
            ),
            "script_protocol_deviations": (),
            "evidence_records": (),
            "evidence_resolutions": (),
            "evidence_integrity": EvidenceIntegrityStatus.VALID,
            "evidence_problems": (),
        }
        return CaseObservation.model_validate(
            {
                **payload,
                "observation_sha256": canonical_sha256(payload),
            }
        )


def _assert_behavior_snapshot(
    seed: PressureSeed,
    snapshot: GeneralAgentContextSnapshot,
    behavior: PressureBehaviorArtifact,
) -> None:
    if (
        behavior.pressure_seed_hash != seed.content_hash
        or behavior.context_snapshot_id != snapshot.snapshot_id
        or behavior.context_snapshot_sha256 != snapshot.content_sha256
    ):
        raise ValueError("压力行为产物与 seed/context snapshot 身份不一致。")


def _pressure_invalid_memory_state(
    value: PressureMemoryValidity,
) -> PressureInvalidMemoryState:
    if value == "active":
        raise ValueError("ACTIVE 记忆不能作为失效哨兵投影。")
    return value


def _necessary_plan_node_ids(
    seed: PressureSeed,
    execution_plan: GeneralAgentExecutionPlan,
) -> set[str]:
    necessary = {item.node_id for item in seed.node_artifacts if item.direct_dependency}
    changed = True
    while changed:
        changed = False
        for node in execution_plan.nodes:
            if node.node_id in necessary:
                continue
            if any(dependency in necessary for dependency in node.dependencies):
                necessary.add(node.node_id)
                changed = True
    return necessary


def _assert_refusal_owners(
    plan: PressurePlan,
    seed: PressureSeed,
    artifact: PressureUnsafeRefusalArtifact,
) -> None:
    if (
        plan.kind is not PressureKind.UNSAFE_TOTAL
        or seed.pressure_plan_id != plan.plan_id
        or seed.pressure_plan_hash != plan.content_hash
        or artifact.pressure_plan_ref != plan.plan_id
        or artifact.pressure_plan_hash != plan.content_hash
        or artifact.pressure_seed_hash != seed.content_hash
    ):
        raise ValueError("安全拒绝产物与 PressurePlan/PressureSeed 身份不一致。")


def _assert_pressure_owners(
    plan: PressurePlan,
    seed: PressureSeed,
    snapshot: GeneralAgentContextSnapshot,
) -> None:
    if (
        seed.pressure_plan_id != plan.plan_id
        or seed.pressure_plan_hash != plan.content_hash
        or seed.kind is not plan.kind
    ):
        raise ValueError("压力种子与 PressurePlan 身份不一致。")
    if snapshot.assembly_trace is None:
        raise ValueError("压力投影要求新上下文快照携带 AssemblyTrace。")


def _carrier_text(
    snapshot: GeneralAgentContextSnapshot,
    carrier: PressureContextCarrier,
) -> str:
    envelope = snapshot.envelope
    if carrier == "stable_memory":
        value: Any = envelope.stable_memory
    elif carrier == "working_memory":
        value = envelope.working_memory.model_dump(mode="json")
    elif carrier == "long_term_memory":
        value = [item.model_dump(mode="json") for item in envelope.long_term_memory]
    elif carrier == "history_memory":
        value = envelope.history_memory.model_dump(mode="json")
    else:
        value = envelope.current_request.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _protected_fact_source_refs(
    seed: PressureSeed,
    fact_ref: str,
) -> tuple[str, ...]:
    refs: list[str] = []
    candidates: tuple[
        PressureHistorySeed
        | PressureMemorySeed
        | PressureRetrievalFragmentSeed
        | PressureNodeArtifactSeed,
        ...,
    ] = (
        *seed.history_messages,
        *seed.working_memories,
        *seed.retrieval_fragments,
        *seed.node_artifacts,
    )
    for item in candidates:
        if fact_ref in item.protected_fact_refs:
            refs.extend(item.source_refs)
    return tuple(dict.fromkeys(refs))


def _node_summaries_by_id(
    snapshot: GeneralAgentContextSnapshot,
) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("node_id")): item
        for item in snapshot.envelope.working_memory.node_summaries
        if isinstance(item, dict) and item.get("node_id")
    }


def _node_projections_by_id(
    snapshot: GeneralAgentContextSnapshot,
) -> dict[str, Any]:
    trace = snapshot.assembly_trace
    if trace is None:
        return {}
    return {item.node_id: item for item in trace.projections}


def _direct_node_contract_preserved(
    snapshot: GeneralAgentContextSnapshot,
    expected: PressureNodeArtifactSeed,
) -> bool:
    summary = _node_summaries_by_id(snapshot).get(expected.node_id)
    if summary is None:
        return False
    return (
        bool(summary.get("result_ref"))
        and set(expected.source_refs) <= set(summary.get("source_refs", ()))
        and set(expected.artifact_refs) <= set(summary.get("artifact_refs", ()))
        and all(
            _projected_path_available(summary.get("output_summary"), path)
            for path in expected.required_output_paths
        )
    )


def _necessary_execution_chain_completed(
    snapshot: GeneralAgentContextSnapshot,
    direct_nodes: tuple[PressureNodeArtifactSeed, ...],
) -> bool:
    plan_summary = snapshot.envelope.working_memory.plan_summary
    if not isinstance(plan_summary, dict):
        return False
    plan_nodes = plan_summary.get("节点")
    if not isinstance(plan_nodes, list):
        return False
    dependency_rows = tuple(row for row in plan_nodes if isinstance(row, dict))
    for direct in direct_nodes:
        if not _direct_node_contract_preserved(
            snapshot, direct
        ) or _projected_required_count(snapshot, direct) != _required_item_count(
            direct
        ):
            return False
        if not any(
            direct.node_id in row.get("依赖", ())
            for row in dependency_rows
            if isinstance(row.get("依赖", ()), (list, tuple))
        ):
            return False
    return True


def _projected_path_available(output_summary: Any, path: str) -> bool:
    if not isinstance(output_summary, dict):
        return False
    if output_summary.get("已截断"):
        return path.split(".")[0] in output_summary.get("内容预览", {}).get("字段", {})
    found, _ = _resolve_seed_path(output_summary, path)
    return found


def _required_item_count(expected: PressureNodeArtifactSeed) -> int:
    if not expected.required_output_paths:
        return 0
    found, value = _resolve_seed_path(
        expected.output,
        expected.required_output_paths[0],
    )
    return len(value) if found and isinstance(value, list) else 0


def _projected_required_count(
    snapshot: GeneralAgentContextSnapshot,
    expected: PressureNodeArtifactSeed,
) -> int | None:
    summary = _node_summaries_by_id(snapshot).get(expected.node_id)
    if summary is None or not expected.required_output_paths:
        return None
    path = expected.required_output_paths[0]
    output_summary = summary.get("output_summary")
    if not isinstance(output_summary, dict):
        return None
    if output_summary.get("已截断"):
        item = output_summary.get("内容预览", {}).get("字段", {}).get(path)
        count = item.get("总条目数") if isinstance(item, dict) else None
        return count if isinstance(count, int) else None
    found, value = _resolve_seed_path(output_summary, path)
    return len(value) if found and isinstance(value, list) else None


def _resolve_seed_path(value: Any, path: str) -> tuple[bool, Any]:
    current = value
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
            continue
        if isinstance(current, list) and segment.isdigit():
            index = int(segment)
            if 0 <= index < len(current):
                current = current[index]
                continue
        return False, None
    return True, current


def _omission_priority_is_valid(
    seed: PressureSeed, snapshot: GeneralAgentContextSnapshot
) -> bool:
    trace = snapshot.assembly_trace
    return trace is not None and not (
        set(trace.protected_refs) & set(trace.omitted_item_refs)
    )


def _session_items(snapshot: GeneralAgentContextSnapshot) -> list[dict[str, Any]]:
    return [
        item
        for items in snapshot.envelope.working_memory.session_memory.model_dump(
            mode="json"
        ).values()
        for item in items.values()
    ]


def _expected_working_projection(seed: PressureSeed) -> dict[str, Any]:
    protected_memories = [
        {
            "seed_id": item.seed_id,
            "kind": item.kind,
            "content": item.content,
            "source_refs": item.source_refs,
        }
        for item in seed.working_memories
        if item.protected_fact_refs
    ]
    direct_nodes = [
        {
            "node_id": item.node_id,
            "required_output_paths": item.required_output_paths,
            "source_refs": item.source_refs,
            "artifact_refs": item.artifact_refs,
            "item_count": _required_item_count(item),
        }
        for item in seed.node_artifacts
        if item.direct_dependency
    ]
    return {
        "protected_memories": protected_memories,
        "direct_nodes": direct_nodes,
    }


def _actual_working_projection(
    seed: PressureSeed,
    snapshot: GeneralAgentContextSnapshot,
) -> dict[str, Any]:
    actual_memories = _session_items(snapshot)
    protected_memories = []
    for item in seed.working_memories:
        if not item.protected_fact_refs:
            continue
        if any(actual["content"] == item.content for actual in actual_memories):
            protected_memories.append(
                {
                    "seed_id": item.seed_id,
                    "kind": item.kind,
                    "content": item.content,
                    "source_refs": item.source_refs,
                }
            )
    direct_nodes = []
    for node in seed.node_artifacts:
        if not node.direct_dependency:
            continue
        if _direct_node_contract_preserved(snapshot, node):
            direct_nodes.append(
                {
                    "node_id": node.node_id,
                    "required_output_paths": node.required_output_paths,
                    "source_refs": node.source_refs,
                    "artifact_refs": node.artifact_refs,
                    "item_count": _projected_required_count(snapshot, node),
                }
            )
    return {
        "protected_memories": protected_memories,
        "direct_nodes": direct_nodes,
    }


def _working_protected_refs(seed: PressureSeed) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [
                *[
                    item.fact_ref
                    for item in seed.protected_facts
                    if "working_memory" in item.required_carriers
                ],
                *[
                    item.seed_id
                    for item in seed.working_memories
                    if item.protected_fact_refs
                ],
                *[
                    item.node_id
                    for item in seed.node_artifacts
                    if item.direct_dependency
                ],
            ]
        )
    )


def _expected_history_projection(seed: PressureSeed) -> dict[str, Any]:
    protected = [
        {
            "seed_id": item.seed_id,
            "content": item.content,
            "source_refs": item.source_refs,
        }
        for item in seed.history_messages
        if item.protected_fact_refs
    ]
    recent = (
        {
            "seed_id": seed.history_messages[-1].seed_id,
            "content": seed.history_messages[-1].content,
            "source_refs": seed.history_messages[-1].source_refs,
        }
        if seed.history_messages
        else None
    )
    return {"protected_messages": protected, "recent_raw": recent}


def _actual_history_projection(
    seed: PressureSeed,
    snapshot: GeneralAgentContextSnapshot,
) -> dict[str, Any]:
    history = snapshot.envelope.history_memory
    history_text = "\n".join(
        [history.summary, *[item.content for item in history.messages]]
    )
    protected = [
        {
            "seed_id": item.seed_id,
            "content": item.content,
            "source_refs": item.source_refs,
        }
        for item in seed.history_messages
        if item.protected_fact_refs and item.content in history_text
    ]
    raw_contents = {item.content for item in history.messages}
    recent = None
    if seed.history_messages and seed.history_messages[-1].content in raw_contents:
        item = seed.history_messages[-1]
        recent = {
            "seed_id": item.seed_id,
            "content": item.content,
            "source_refs": item.source_refs,
        }
    return {"protected_messages": protected, "recent_raw": recent}


def _history_protected_refs(seed: PressureSeed) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [
                *[
                    item.fact_ref
                    for item in seed.protected_facts
                    if "history_memory" in item.required_carriers
                ],
                *[
                    item.seed_id
                    for item in seed.history_messages
                    if item.protected_fact_refs
                ],
                *(["recent_history"] if seed.history_messages else []),
            ]
        )
    )


DeterministicPressureSeedGenerator = PressureSeedGenerator
PressureCarrier = PressureKind


__all__ = [
    "DeterministicPressureSeedGenerator",
    "PressureBehaviorArtifact",
    "PressureBehaviorAssertionResult",
    "PressureBehaviorCheck",
    "PressureBehaviorEvaluator",
    "PressureBehaviorOracle",
    "PressureCarrier",
    "PressureContextPreservationProjector",
    "PressureFixtureBlob",
    "PressureHistorySeed",
    "PressureKind",
    "PressureMemorySeed",
    "PressureNodeArtifactSeed",
    "PressurePlan",
    "PressureProtectedFactSeed",
    "PressureRetrievalFragmentSeed",
    "PressureSeed",
    "PressureSeedGenerator",
]
