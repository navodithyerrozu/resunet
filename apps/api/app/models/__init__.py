"""Model layer.

Re-exported here so call sites can `from app.models import Bullet` rather
than reaching into individual modules. Import from this package, not from
submodules, so the internal file layout stays free to change.
"""

from app.models.agent import (
    AgentThread,
    BulletProposal,
    ChatMessage,
    GenerateStartRequest,
    GenerateTurnRequest,
    GenerateTurnResponse,
    MessageRole,
    ThreadKind,
    ThreadStatus,
)
from app.models.application import (
    Application,
    ApplicationCreate,
    ApplicationEvent,
    ApplicationStatus,
    ApplicationStatusUpdate,
    ApplicationSummary,
    AttemptOutcome,
    BlockReason,
    BoardCredential,
    FieldMapping,
    SubmissionAttempt,
    SubmissionMethod,
)
from app.models.ats import (
    ATSFinding,
    ATSScore,
    DimensionScore,
    FindingSeverity,
    KeywordAnalysis,
    ScoreComparison,
    ScoreDimension,
    ScoreRequest,
)
from app.models.base import (
    ApiModel,
    AppModel,
    MongoModel,
    Page,
    PyObjectId,
    SortDirection,
    utcnow,
)
from app.models.bullet import (
    Bullet,
    BulletDraft,
    BulletSource,
    BulletStatus,
    XYZViolation,
    XYZViolationCode,
)
from app.models.job import (
    Compensation,
    Job,
    JobBoard,
    JobIngestRequest,
    JobMatch,
    JobSource,
    JobStatus,
    JobSummary,
    KeywordWeight,
    MatchComponents,
    ParsedJob,
    RemoteType,
    Seniority,
)
from app.models.profile import (
    CareerProfile,
    Certification,
    ContactBlock,
    DateRange,
    EducationEntry,
    EmploymentType,
    ExperienceEntry,
    ProjectEntry,
    Skill,
    SkillCount,
    SkillProficiency,
)
from app.models.resume import (
    RenderedBullet,
    RenderedEntry,
    RenderedSection,
    RenderJob,
    RenderJobStatus,
    RenderRequest,
    Resume,
    ResumeCreate,
    ResumeSection,
    ResumeStatus,
    ResumeSummary,
    ResumeTemplate,
    ResumeUpdate,
    ResumeVersion,
    SectionKind,
)
from app.models.user import (
    TokenPair,
    TokenPayload,
    User,
    UserCreate,
    UserLogin,
    UserPublic,
    UserRole,
    UserStatus,
)

__all__ = [
    # base
    "ApiModel", "AppModel", "MongoModel", "Page", "PyObjectId", "SortDirection", "utcnow",
    # user
    "TokenPair", "TokenPayload", "User", "UserCreate", "UserLogin", "UserPublic",
    "UserRole", "UserStatus",
    # profile
    "CareerProfile", "Certification", "ContactBlock", "DateRange", "EducationEntry",
    "EmploymentType", "ExperienceEntry", "ProjectEntry", "Skill", "SkillCount",
    "SkillProficiency",
    # bullet
    "Bullet", "BulletDraft", "BulletSource", "BulletStatus", "XYZViolation",
    "XYZViolationCode",
    # resume
    "RenderJob", "RenderJobStatus", "RenderRequest", "RenderedBullet", "RenderedEntry",
    "RenderedSection", "Resume", "ResumeCreate", "ResumeSection", "ResumeStatus",
    "ResumeSummary", "ResumeTemplate", "ResumeUpdate", "ResumeVersion", "SectionKind",
    # job
    "Compensation", "Job", "JobBoard", "JobIngestRequest", "JobMatch", "JobSource",
    "JobStatus", "JobSummary", "KeywordWeight", "MatchComponents", "ParsedJob",
    "RemoteType", "Seniority",
    # ats
    "ATSFinding", "ATSScore", "DimensionScore", "FindingSeverity", "KeywordAnalysis",
    "ScoreComparison", "ScoreDimension", "ScoreRequest",
    # application
    "Application", "ApplicationCreate", "ApplicationEvent", "ApplicationStatus",
    "ApplicationStatusUpdate", "ApplicationSummary", "AttemptOutcome", "BlockReason",
    "BoardCredential", "FieldMapping", "SubmissionAttempt", "SubmissionMethod",
    # agent
    "AgentThread", "BulletProposal", "ChatMessage", "GenerateStartRequest",
    "GenerateTurnRequest", "GenerateTurnResponse", "MessageRole", "ThreadKind",
    "ThreadStatus",
]
