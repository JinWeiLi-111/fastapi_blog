import pytest
from httpx import AsyncClient

from tests.conftest import auth_header, create_test_user, login_user


@pytest.mark.anyio
async def test_get_posts_empty(client: AsyncClient) -> None:
    response = await client.get("/api/posts")

    assert response.status_code == 200
    data = response.json()
    assert data["posts"] == []
    assert data["total"] == 0
    assert not data["has_more"]


@pytest.mark.anyio
async def test_get_posts_not_found(client: AsyncClient) -> None:
    response = await client.get("/api/posts/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Post not found"


@pytest.mark.anyio
async def test_create_post_success(client: AsyncClient) -> None:
    user = await create_test_user(client)
    token = await login_user(client)
    headers = auth_header(token)

    response = await client.post(
        "/api/posts",
        json={"title": "My First Post", "content": "This is the content"},
        headers=headers,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "My First Post"
    assert data["content"] == "This is the content"
    assert data["user_id"] == user["id"]
    assert "id" in data
    assert "date_posted" in data
    assert data["author"]["username"] == "testuser"


@pytest.mark.anyio
async def test_create_post_unauthorized(client: AsyncClient) -> None:
    response = await client.post(
        "/api/posts",
        json={"title": "Test Post", "content": "Test Conetent"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


@pytest.mark.anyio
async def test_update_post_success(client: AsyncClient) -> None:
    await create_test_user(client)
    token = await login_user(client)
    headers = auth_header(token)

    response = await client.post(
        "/api/posts",
        json={"title": "Original Title", "content": "Original Conetent"},
        headers=headers,
    )
    post_id = response.json()["id"]

    response = await client.patch(
        f"/api/posts/{post_id}",
        json={"title": "Updated Title"},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["content"] == "Original Conetent"


@pytest.mark.anyio
async def test_update_post_wrong_user(client: AsyncClient) -> None:
    await create_test_user(client)
    token = await login_user(client)
    headers = auth_header(token)

    response = await client.post(
        "/api/posts",
        json={"title": "Original Title", "content": "Original Conetent"},
        headers=headers,
    )
    post_id = response.json()["id"]

    await create_test_user(client, username="testuser1", email="test1@example.com")
    token = await login_user(client, email="test1@example.com")
    headers = auth_header(token)

    response = await client.patch(
        f"/api/posts/{post_id}",
        json={"title": "Updated Title"},
        headers=headers,
    )

    assert response.status_code == 403
    assert (
        response.json()["detail"]
        == "You are not the owner of this post, not authorized to update it"
    )


@pytest.mark.anyio
async def test_get_posts_with_pagination(client: AsyncClient) -> None:
    await create_test_user(client)
    token = await login_user(client)
    # wrap the token
    headers = auth_header(token)

    for i in range(12):
        response = await client.post(
            "/api/posts",
            json={"title": f"Post {i}", "content": "Content for post{i}"},
            headers=headers,
        )
        assert response.status_code == 201

    response = await client.get("/api/posts")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 12
    assert len(data["posts"]) == 10
    assert data["has_more"] is True

    response = await client.get("/api/posts?skip=2&limit=8")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 12
    assert len(data["posts"]) == 8
    assert data["has_more"] is True
