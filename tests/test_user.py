from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.conftest import auth_header, create_test_user, login_user


@pytest.mark.anyio
async def test_create_user_validation_error(client: AsyncClient) -> None:
    response = await client.post(
        "/api/users",
        json={
            "username": "testuser",
        },
    )

    assert response.status_code == 422
    assert "email" in response.text
    assert "password" in response.text


@pytest.mark.anyio
async def test_create_user_duplicate_email(client: AsyncClient) -> None:
    await create_test_user(
        client,
        username="testuser",
        email="testuser@example.com",
        password="testuser1",
    )

    response = await client.post(
        "/api/users",
        json={
            "username": "testuser",
            "email": "testuser@example.com",
            "password": "testuser1",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Username already exists"

    response = await client.post(
        "/api/users",
        json={
            "username": "testuser2",
            "email": "testuser@example.com",
            "password": "testuser1",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Email already exists"


@pytest.mark.anyio
async def test_create_user_success(client: AsyncClient) -> None:
    response = await client.post(
        "/api/users",
        json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "securepassword123",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "newuser"
    assert data["email"] == "newuser@example.com"
    assert "password" not in data
    assert "password_hash" not in data
    assert "id" in data
    assert "image_path" in data


@pytest.mark.anyio
async def test_upload_profile_picture(client: AsyncClient, mocked_aws) -> None:
    user = await create_test_user(client)
    token = await login_user(client)
    headers = auth_header(token)

    test_image_path = Path(__file__).parent / "test_profile_pics" / "test_image.jpg"

    # load the image file as a raw bytes object
    image_bytes = test_image_path.read_bytes()

    response = await client.patch(
        f"api/users/{user['id']}/picture",
        files={"file": ("profile.jpg", BytesIO(image_bytes), "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["image_file"] is not None
    assert data["image_file"].endswith(".jpg")
    assert "s3" in data["image_path"]

    s3_objects = mocked_aws.list_objects_v2(Bucket="test-bucket")
    assert "Contents" in s3_objects
    assert len(s3_objects["Contents"]) == 1
    assert s3_objects["Contents"][0]["Key"].endswith(data["image_file"])


@pytest.mark.anyio
async def test_forgot_password_sends_email(client: AsyncClient) -> None:
    await create_test_user(client)

    with patch(
        "fastapi_blog.routers.users.send_password_reset_email",
        new_callable=AsyncMock,
    ) as mock_send:
        response = await client.post()
