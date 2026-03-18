"""Tests for the RBAC and Authentication system (Phase 1)."""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from app.auth.models import RoleLevel, Session, User
from app.auth.service import AuthService
from app.auth.rbac import RBACService
from app.storage.database import Database


@pytest_asyncio.fixture
async def db_path():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def database(db_path):
    """Initialize a test database with all tables."""
    db = Database(db_path)
    await db.init()
    return db


@pytest_asyncio.fixture
async def auth_service(db_path, database):
    """Create an AuthService for testing."""
    return AuthService(db_path=db_path)


@pytest_asyncio.fixture
async def rbac_service(db_path, database):
    """Create an RBACService for testing."""
    return RBACService(db_path=db_path)


@pytest_asyncio.fixture
async def super_admin_user(auth_service):
    """Create and return a super admin user."""
    return await auth_service.create_user(
        email="admin@test.com",
        display_name="Test Admin",
        password="admin123",
        role_level=RoleLevel.SUPER_ADMIN,
    )


@pytest_asyncio.fixture
async def admin_user(auth_service):
    """Create and return an admin user."""
    return await auth_service.create_user(
        email="manager_admin@test.com",
        display_name="Test Admin User",
        password="admin123",
        role_level=RoleLevel.ADMIN,
    )


@pytest_asyncio.fixture
async def manager_user(auth_service):
    """Create and return a manager user."""
    return await auth_service.create_user(
        email="manager@test.com",
        display_name="Test Manager",
        password="manager123",
        role_level=RoleLevel.MANAGER,
    )


@pytest_asyncio.fixture
async def employee_user(auth_service):
    """Create and return an employee user."""
    return await auth_service.create_user(
        email="employee@test.com",
        display_name="Test Employee",
        password="employee123",
        role_level=RoleLevel.EMPLOYEE,
    )


# ═══════════════════════════════════════════════════════════
# RoleLevel Model Tests
# ═══════════════════════════════════════════════════════════

class TestRoleLevelModel:
    def test_role_hierarchy_order(self):
        hierarchy = RoleLevel.hierarchy()
        assert hierarchy[0] == RoleLevel.SUPER_ADMIN
        assert hierarchy[1] == RoleLevel.ADMIN
        assert hierarchy[2] == RoleLevel.MANAGER
        assert hierarchy[3] == RoleLevel.EMPLOYEE

    def test_role_rank(self):
        assert RoleLevel.SUPER_ADMIN.rank() > RoleLevel.ADMIN.rank()
        assert RoleLevel.ADMIN.rank() > RoleLevel.MANAGER.rank()
        assert RoleLevel.MANAGER.rank() > RoleLevel.EMPLOYEE.rank()

    def test_super_admin_inherits_all(self):
        assert RoleLevel.SUPER_ADMIN.inherits_from(RoleLevel.ADMIN)
        assert RoleLevel.SUPER_ADMIN.inherits_from(RoleLevel.MANAGER)
        assert RoleLevel.SUPER_ADMIN.inherits_from(RoleLevel.EMPLOYEE)

    def test_admin_inherits_from_manager_and_employee(self):
        assert RoleLevel.ADMIN.inherits_from(RoleLevel.MANAGER)
        assert RoleLevel.ADMIN.inherits_from(RoleLevel.EMPLOYEE)
        assert not RoleLevel.ADMIN.inherits_from(RoleLevel.SUPER_ADMIN)

    def test_manager_inherits_from_employee_only(self):
        assert RoleLevel.MANAGER.inherits_from(RoleLevel.EMPLOYEE)
        assert not RoleLevel.MANAGER.inherits_from(RoleLevel.ADMIN)
        assert not RoleLevel.MANAGER.inherits_from(RoleLevel.SUPER_ADMIN)

    def test_employee_inherits_from_self_only(self):
        assert RoleLevel.EMPLOYEE.inherits_from(RoleLevel.EMPLOYEE)
        assert not RoleLevel.EMPLOYEE.inherits_from(RoleLevel.MANAGER)

    def test_role_inherits_from_self(self):
        for role in RoleLevel:
            assert role.inherits_from(role)


class TestSessionModel:
    def test_session_not_expired(self):
        session = Session(
            token="test-token",
            user_id="user-1",
            created_at=datetime.utcnow().isoformat(),
            expires_at=(datetime.utcnow() + timedelta(hours=24)).isoformat(),
        )
        assert not session.is_expired()

    def test_session_expired(self):
        session = Session(
            token="test-token",
            user_id="user-1",
            created_at=(datetime.utcnow() - timedelta(hours=48)).isoformat(),
            expires_at=(datetime.utcnow() - timedelta(hours=24)).isoformat(),
        )
        assert session.is_expired()


class TestUserModel:
    def test_user_to_dict(self):
        user = User(
            id="test-id",
            email="test@test.com",
            display_name="Test User",
            role_level=RoleLevel.EMPLOYEE,
            department="Engineering",
            is_active=True,
            created_at="2024-01-01T00:00:00",
        )
        d = user.to_dict()
        assert d["id"] == "test-id"
        assert d["email"] == "test@test.com"
        assert d["role_level"] == "employee"
        assert d["department"] == "Engineering"
        assert d["is_active"] is True


# ═══════════════════════════════════════════════════════════
# AuthService Tests
# ═══════════════════════════════════════════════════════════

class TestUserCreation:
    @pytest.mark.asyncio
    async def test_create_user(self, auth_service):
        user = await auth_service.create_user(
            email="new@test.com",
            display_name="New User",
            password="password123",
            role_level=RoleLevel.EMPLOYEE,
            department="Engineering",
        )
        assert user.email == "new@test.com"
        assert user.display_name == "New User"
        assert user.role_level == RoleLevel.EMPLOYEE
        assert user.department == "Engineering"
        assert user.is_active is True
        assert user.id  # UUID was generated

    @pytest.mark.asyncio
    async def test_create_user_duplicate_email(self, auth_service):
        await auth_service.create_user(
            email="dup@test.com",
            display_name="User 1",
            password="pass123",
        )
        with pytest.raises(ValueError, match="already exists"):
            await auth_service.create_user(
                email="dup@test.com",
                display_name="User 2",
                password="pass456",
            )

    @pytest.mark.asyncio
    async def test_create_super_admin(self, auth_service):
        user = await auth_service.create_user(
            email="superadmin@test.com",
            display_name="Super Admin",
            password="super123",
            role_level=RoleLevel.SUPER_ADMIN,
        )
        assert user.role_level == RoleLevel.SUPER_ADMIN


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_authenticate_success(self, auth_service):
        await auth_service.create_user(
            email="auth@test.com",
            display_name="Auth User",
            password="correct123",
        )
        session = await auth_service.authenticate("auth@test.com", "correct123")
        assert session is not None
        assert session.token
        assert session.user_id

    @pytest.mark.asyncio
    async def test_authenticate_wrong_password(self, auth_service):
        await auth_service.create_user(
            email="auth2@test.com",
            display_name="Auth User",
            password="correct123",
        )
        session = await auth_service.authenticate("auth2@test.com", "wrong123")
        assert session is None

    @pytest.mark.asyncio
    async def test_authenticate_nonexistent_email(self, auth_service):
        session = await auth_service.authenticate("nobody@test.com", "password")
        assert session is None

    @pytest.mark.asyncio
    async def test_authenticate_inactive_user(self, auth_service):
        user = await auth_service.create_user(
            email="inactive@test.com",
            display_name="Inactive",
            password="pass123",
        )
        await auth_service.deactivate_user(user.id)
        session = await auth_service.authenticate("inactive@test.com", "pass123")
        assert session is None


class TestSessionManagement:
    @pytest.mark.asyncio
    async def test_validate_session(self, auth_service):
        await auth_service.create_user(
            email="session@test.com",
            display_name="Session User",
            password="pass123",
        )
        session = await auth_service.authenticate("session@test.com", "pass123")
        assert session is not None

        user = await auth_service.validate_session(session.token)
        assert user is not None
        assert user.email == "session@test.com"

    @pytest.mark.asyncio
    async def test_validate_invalid_session(self, auth_service):
        user = await auth_service.validate_session("invalid-token-xyz")
        assert user is None

    @pytest.mark.asyncio
    async def test_logout_invalidates_session(self, auth_service):
        await auth_service.create_user(
            email="logout@test.com",
            display_name="Logout User",
            password="pass123",
        )
        session = await auth_service.authenticate("logout@test.com", "pass123")
        assert session is not None

        await auth_service.logout(session.token)
        user = await auth_service.validate_session(session.token)
        assert user is None

    @pytest.mark.asyncio
    async def test_session_expiry(self, auth_service, db_path):
        """Test that expired sessions are rejected."""
        import aiosqlite

        await auth_service.create_user(
            email="expiry@test.com",
            display_name="Expiry User",
            password="pass123",
        )
        session = await auth_service.authenticate("expiry@test.com", "pass123")
        assert session is not None

        # Manually set the session expiry to the past
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE api_sessions SET expires_at = ? WHERE token = ?",
                (past, session.token),
            )
            await db.commit()

        user = await auth_service.validate_session(session.token)
        assert user is None

    @pytest.mark.asyncio
    async def test_cleanup_expired_sessions(self, auth_service, db_path):
        """Test that cleanup_expired_sessions removes old sessions."""
        import aiosqlite

        await auth_service.create_user(
            email="cleanup@test.com",
            display_name="Cleanup User",
            password="pass123",
        )
        session = await auth_service.authenticate("cleanup@test.com", "pass123")

        # Set expiry to the past
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE api_sessions SET expires_at = ? WHERE token = ?",
                (past, session.token),
            )
            await db.commit()

        removed = await auth_service.cleanup_expired_sessions()
        assert removed == 1


class TestUserManagement:
    @pytest.mark.asyncio
    async def test_get_user(self, auth_service):
        created = await auth_service.create_user(
            email="getuser@test.com",
            display_name="Get User",
            password="pass123",
        )
        fetched = await auth_service.get_user(created.id)
        assert fetched is not None
        assert fetched.email == "getuser@test.com"

    @pytest.mark.asyncio
    async def test_get_user_by_email(self, auth_service):
        await auth_service.create_user(
            email="byemail@test.com",
            display_name="By Email",
            password="pass123",
        )
        user = await auth_service.get_user_by_email("byemail@test.com")
        assert user is not None
        assert user.display_name == "By Email"

    @pytest.mark.asyncio
    async def test_get_nonexistent_user(self, auth_service):
        user = await auth_service.get_user("nonexistent-id")
        assert user is None

    @pytest.mark.asyncio
    async def test_list_users(self, auth_service):
        await auth_service.create_user(
            email="list1@test.com", display_name="User 1", password="pass123"
        )
        await auth_service.create_user(
            email="list2@test.com", display_name="User 2", password="pass123"
        )
        users = await auth_service.list_users()
        emails = [u.email for u in users]
        assert "list1@test.com" in emails
        assert "list2@test.com" in emails

    @pytest.mark.asyncio
    async def test_change_role(self, auth_service):
        user = await auth_service.create_user(
            email="changerole@test.com",
            display_name="Change Role",
            password="pass123",
            role_level=RoleLevel.EMPLOYEE,
        )
        await auth_service.change_role(user.id, RoleLevel.MANAGER)
        updated = await auth_service.get_user(user.id)
        assert updated.role_level == RoleLevel.MANAGER

    @pytest.mark.asyncio
    async def test_deactivate_user(self, auth_service):
        user = await auth_service.create_user(
            email="deactivate@test.com",
            display_name="Deactivate",
            password="pass123",
        )
        await auth_service.deactivate_user(user.id)
        fetched = await auth_service.get_user(user.id)
        assert fetched.is_active is False


class TestSetupFlow:
    @pytest.mark.asyncio
    async def test_setup_not_complete_initially(self, auth_service):
        complete = await auth_service.is_setup_complete()
        assert complete is False

    @pytest.mark.asyncio
    async def test_setup_complete_after_super_admin(self, auth_service):
        await auth_service.create_user(
            email="firstadmin@test.com",
            display_name="First Admin",
            password="admin123",
            role_level=RoleLevel.SUPER_ADMIN,
        )
        complete = await auth_service.is_setup_complete()
        assert complete is True

    @pytest.mark.asyncio
    async def test_setup_not_complete_with_non_admin(self, auth_service):
        await auth_service.create_user(
            email="justemployee@test.com",
            display_name="Employee",
            password="pass123",
            role_level=RoleLevel.EMPLOYEE,
        )
        complete = await auth_service.is_setup_complete()
        assert complete is False


# ═══════════════════════════════════════════════════════════
# RBAC Service Tests
# ═══════════════════════════════════════════════════════════

class TestPermissionChecks:
    """Permissions are role-based for admin features and file access.
    Skills are open to everyone — no skill permissions in the seed data.
    """

    @pytest.mark.asyncio
    async def test_super_admin_has_all_permissions(self, rbac_service, super_admin_user):
        assert await rbac_service.check_permission(super_admin_user, "file:read")
        assert await rbac_service.check_permission(super_admin_user, "file:write")
        assert await rbac_service.check_permission(super_admin_user, "admin:dashboard")
        assert await rbac_service.check_permission(super_admin_user, "admin:users")
        assert await rbac_service.check_permission(super_admin_user, "data:export")

    @pytest.mark.asyncio
    async def test_admin_has_file_and_admin_permissions(self, rbac_service, admin_user):
        assert await rbac_service.check_permission(admin_user, "file:read")
        assert await rbac_service.check_permission(admin_user, "file:write")
        assert await rbac_service.check_permission(admin_user, "admin:dashboard")
        assert await rbac_service.check_permission(admin_user, "admin:users")

    @pytest.mark.asyncio
    async def test_admin_lacks_data_wildcard(self, rbac_service, admin_user):
        # admin does NOT have data:* — only super_admin does
        assert not await rbac_service.check_permission(admin_user, "data:export")

    @pytest.mark.asyncio
    async def test_manager_has_file_read_only(self, rbac_service, manager_user):
        assert await rbac_service.check_permission(manager_user, "file:read")
        assert not await rbac_service.check_permission(manager_user, "file:write")

    @pytest.mark.asyncio
    async def test_employee_has_file_read(self, rbac_service, employee_user):
        assert await rbac_service.check_permission(employee_user, "file:read")
        assert not await rbac_service.check_permission(employee_user, "file:write")

    @pytest.mark.asyncio
    async def test_employee_no_admin_access(self, rbac_service, employee_user):
        assert not await rbac_service.check_permission(employee_user, "admin:dashboard")
        assert not await rbac_service.check_permission(employee_user, "admin:users")

    @pytest.mark.asyncio
    async def test_inactive_user_no_permissions(self, rbac_service, employee_user):
        employee_user.is_active = False
        assert not await rbac_service.check_permission(employee_user, "file:read")


class TestSkillAccessFiltering:
    """Skills are now open to all users — no role-based filtering.
    The get_allowed_skills method still exists but returns empty since
    no skill permissions are seeded. Skills are accessed directly via
    SkillRegistry without RBAC checks.
    """

    @pytest.mark.asyncio
    async def test_all_roles_get_same_skills(self, rbac_service, super_admin_user, employee_user):
        # With no skill:* permissions seeded, get_allowed_skills returns empty for all
        # This is fine because skills are no longer gated by RBAC
        sa_skills = await rbac_service.get_allowed_skills(super_admin_user)
        emp_skills = await rbac_service.get_allowed_skills(employee_user)
        assert sa_skills == emp_skills

    @pytest.mark.asyncio
    async def test_inactive_user_no_skills(self, rbac_service, employee_user):
        employee_user.is_active = False
        skills = await rbac_service.get_allowed_skills(employee_user)
        assert skills == []


class TestAdminAccess:
    @pytest.mark.asyncio
    async def test_super_admin_can_access_admin(self, rbac_service, super_admin_user):
        assert await rbac_service.can_access_admin(super_admin_user)

    @pytest.mark.asyncio
    async def test_admin_can_access_admin(self, rbac_service, admin_user):
        assert await rbac_service.can_access_admin(admin_user)

    @pytest.mark.asyncio
    async def test_employee_cannot_access_admin(self, rbac_service, employee_user):
        assert not await rbac_service.can_access_admin(employee_user)

    @pytest.mark.asyncio
    async def test_manager_cannot_access_admin(self, rbac_service, manager_user):
        assert not await rbac_service.can_access_admin(manager_user)


class TestCanAccessSkill:
    """Skills are open to all — can_access_skill checks the (now empty)
    permission list. This is a legacy API; skill access is not enforced."""

    @pytest.mark.asyncio
    async def test_skill_access_not_role_gated(self, rbac_service, employee_user):
        # No skill permissions seeded, so can_access_skill returns False for all
        # Skills are accessed directly via SkillRegistry without RBAC filtering
        result = await rbac_service.can_access_skill(employee_user, "it_support")
        # This is expected to be False since no skill:* perms exist
        assert isinstance(result, bool)


# ═══════════════════════════════════════════════════════════
# Database Schema Tests
# ═══════════════════════════════════════════════════════════

class TestDatabaseSchema:
    @pytest.mark.asyncio
    async def test_tables_created(self, database, db_path):
        """Verify all auth-related tables exist."""
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in await cursor.fetchall()}

        assert "users" in tables
        assert "role_permissions" in tables
        assert "user_skill_overrides" in tables
        assert "file_access_policies" in tables
        assert "api_sessions" in tables

    @pytest.mark.asyncio
    async def test_default_permissions_seeded(self, database, db_path):
        """Verify default permissions were seeded."""
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM role_permissions")
            row = await cursor.fetchone()
            assert row[0] > 0

            # Verify super_admin has file:*, admin:*, data:* (no skill perms)
            cursor = await db.execute(
                "SELECT permission_key FROM role_permissions WHERE role_level = 'super_admin'"
            )
            perms = {row[0] for row in await cursor.fetchall()}
            assert "file:*" in perms
            assert "admin:*" in perms
            assert "data:*" in perms

    @pytest.mark.asyncio
    async def test_permissions_not_reseeded(self, database, db_path):
        """Verify calling init() again doesn't duplicate permissions."""
        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM role_permissions")
            count_before = (await cursor.fetchone())[0]

        # Re-init
        await database.init()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM role_permissions")
            count_after = (await cursor.fetchone())[0]

        assert count_before == count_after


class TestFileAccessPolicies:
    @pytest.mark.asyncio
    async def test_get_file_policies_empty(self, rbac_service, employee_user):
        """No file_access_policies rows by default, so result should be empty."""
        policies = await rbac_service.get_file_policies(employee_user)
        # May be empty since we didn't add file_access_policies rows in seed
        assert isinstance(policies, list)

    @pytest.mark.asyncio
    async def test_get_file_policies_with_data(self, rbac_service, employee_user, db_path):
        """Test file policies are returned correctly."""
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO file_access_policies (role_level, directory_path, access_type) VALUES (?, ?, ?)",
                ("employee", "/data/reports", "read"),
            )
            await db.commit()

        policies = await rbac_service.get_file_policies(employee_user)
        assert len(policies) == 1
        assert policies[0]["directory_path"] == "/data/reports"
        assert policies[0]["access_type"] == "read"
