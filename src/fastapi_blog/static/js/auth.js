let currentUser = null;
let fetchPromise = null;

export async function getCurrentUser() {
  if (currentUser) {
    return currentUser;
  }

  // Return in-progress fetch to prevent duplicate API calls
  if (fetchPromise) {
    return fetchPromise;
  }

  const token = localStorage.getItem("access_token");
  if (!token) {
    return null;
  }

  fetchPromise = (async () => {
    try {
      const response = await fetch("/api/users/me", {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      if (response.ok) {
        currentUser = await response.json();
        return currentUser;
      }

      localStorage.removeItem("access_token");
      return null;
    } catch (error) {
      console.error("Error fetching current user:", error);
      return null;
    } finally {
      fetchPromise = null;
    }
  })();

  return fetchPromise;
}

export function logout() {
  // user log out and remove the access token from local storage
  localStorage.removeItem("access_token");
  currentUser = null;
  window.location.href = "/";
}

export function getToken() {
    // get the access token of log-in user from local storage
  return localStorage.getItem("access_token");
}

export function setToken(token) {
    // set the access token of log-in user to local storage
  localStorage.setItem("access_token", token);
}

export function clearUserCache() {
    // clear the buffered user data
  currentUser = null;
}