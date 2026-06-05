# phases/backend_language_profiles.py
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class BackendLanguageProfile:
    name: str
    extensions: tuple[str, ...]
    first_pass_frameworks: tuple[str, ...]
    parser_strategy: str
    future_parser: str
    entrypoint_types: tuple[str, ...]
    node_types: tuple[str, ...]
    edge_types: tuple[str, ...]
    high_risk_signals: tuple[str, ...]


_PROFILES: dict[str, BackendLanguageProfile] = {
    "csharp": BackendLanguageProfile(
        name="csharp",
        extensions=(".cs",),
        first_pass_frameworks=("ASP.NET Core Controller",),
        parser_strategy="lexical_with_brace_awareness",
        future_parser="Roslyn",
        entrypoint_types=("aspnet_controller_action",),
        node_types=("controller_action", "service_method", "repository_method", "model_property", "entity_property", "db_context", "middleware", "authorize_attribute"),
        edge_types=("exposes_endpoint", "calls", "uses_model", "writes_db", "requires_auth", "calls_external_api", "reads_config"),
        high_risk_signals=("Authorize", "HttpPost", "nullable_changed", "required_changed", "SaveChanges", "transaction_changed", "external_http_call"),
    ),
    "java": BackendLanguageProfile(
        name="java",
        extensions=(".java",),
        first_pass_frameworks=("Spring MVC", "Spring Boot"),
        parser_strategy="lexical_annotation_scanner",
        future_parser="JavaParser or tree-sitter-java",
        entrypoint_types=("spring_controller", "spring_rest_controller"),
        node_types=("controller_method", "service_method", "repository_method", "dto_field", "entity_field", "mapper_method", "filter", "interceptor"),
        edge_types=("exposes_endpoint", "calls", "uses_dto", "writes_db", "requires_auth", "transaction_boundary", "throws_exception"),
        high_risk_signals=("@RequestMapping", "@GetMapping", "@PostMapping", "@Transactional", "@PreAuthorize", "@Valid", "JpaRepository", "save(", "delete("),
    ),
    "python": BackendLanguageProfile(
        name="python",
        extensions=(".py",),
        first_pass_frameworks=("FastAPI", "Django", "Flask"),
        parser_strategy="python_ast_plus_framework_decorators",
        future_parser="Python ast with framework-specific visitors",
        entrypoint_types=("fastapi_route", "django_view", "flask_route"),
        node_types=("route_handler", "dependency", "service_function", "repository_function", "schema_field", "orm_model_field", "middleware"),
        edge_types=("exposes_endpoint", "calls", "uses_schema", "writes_db", "requires_auth", "transaction_boundary", "raises_exception"),
        high_risk_signals=("@app.get", "@app.post", "Depends", "permission_classes", "serializer", "BaseModel", "transaction.atomic", ".save(", ".delete("),
    ),
    "nodejs": BackendLanguageProfile(
        name="nodejs",
        extensions=(".js", ".ts", ".mjs", ".cjs"),
        first_pass_frameworks=("Express", "NestJS", "Koa", "Fastify"),
        parser_strategy="typescript_javascript_route_scanner",
        future_parser="Babel parser or tree-sitter-typescript",
        entrypoint_types=("express_route", "nestjs_controller", "koa_route", "fastify_route"),
        node_types=("route_handler", "controller_method", "service_method", "repository_method", "dto_schema", "middleware", "guard", "interceptor"),
        edge_types=("exposes_endpoint", "calls", "uses_schema", "writes_db", "requires_auth", "async_error_path", "calls_external_api"),
        high_risk_signals=(".get(", ".post(", "@Controller", "@Post", "AuthGuard", "middleware", "schema.parse", "await", "transaction", "prisma.", "repository.save"),
    ),
    "go": BackendLanguageProfile(
        name="go",
        extensions=(".go",),
        first_pass_frameworks=("Gin", "Echo", "Fiber", "net/http"),
        parser_strategy="go_handler_scanner",
        future_parser="go/parser and go/ast",
        entrypoint_types=("gin_handler", "echo_handler", "fiber_handler", "http_handler"),
        node_types=("handler_function", "service_function", "repository_function", "request_struct_field", "response_struct_field", "middleware", "goroutine_boundary"),
        edge_types=("exposes_endpoint", "calls", "uses_struct", "writes_db", "requires_auth", "starts_goroutine", "locks_mutex"),
        high_risk_signals=("router.POST", "router.GET", "context.Context", "db.", "tx.", "go ", "mutex", "Lock(", "Unlock(", "json tags changed"),
    ),
    "php": BackendLanguageProfile(
        name="php",
        extensions=(".php",),
        first_pass_frameworks=("Laravel", "Symfony"),
        parser_strategy="php_controller_route_scanner",
        future_parser="nikic/php-parser",
        entrypoint_types=("laravel_controller", "symfony_controller"),
        node_types=("controller_action", "form_request", "service_method", "repository_method", "eloquent_model", "middleware", "policy"),
        edge_types=("exposes_endpoint", "calls", "uses_request", "writes_db", "requires_auth", "policy_check", "throws_exception"),
        high_risk_signals=("Route::", "#[Route", "FormRequest", "authorize(", "Policy", "DB::transaction", "save(", "delete(", "validate("),
    ),
    "cpp": BackendLanguageProfile(
        name="cpp",
        extensions=(".cc", ".cpp", ".cxx", ".h", ".hpp"),
        first_pass_frameworks=("service modules", "RPC handlers", "HTTP handlers"),
        parser_strategy="cpp_signature_and_call_scanner",
        future_parser="clangd or libclang",
        entrypoint_types=("rpc_handler", "http_handler", "service_method"),
        node_types=("handler_function", "service_method", "data_struct_field", "storage_call", "serialization_boundary", "thread_boundary"),
        edge_types=("calls", "uses_struct", "reads_config", "writes_storage", "serializes_payload", "starts_thread", "locks_mutex"),
        high_risk_signals=("pointer_lifetime_changed", "lock_or_thread_boundary_changed", "serialization_schema_changed", "delete ", "std::move", "mutex", "thread", "async", "shared_ptr", "unique_ptr"),
    ),
}


def supported_languages() -> list[str]:
    return list(_PROFILES)


def get_profile(language: str) -> BackendLanguageProfile:
    normalized = language.lower().replace("node.js", "nodejs").replace("c++", "cpp")
    if normalized not in _PROFILES:
        raise ValueError(f"Unsupported backend language: {language}")
    return _PROFILES[normalized]


def profiles_for_extensions(extensions: set[str]) -> list[BackendLanguageProfile]:
    return [
        profile
        for profile in _PROFILES.values()
        if any(ext in extensions for ext in profile.extensions)
    ]
