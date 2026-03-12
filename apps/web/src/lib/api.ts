/**
 * API client for Moose Sports Empire backend communication.
 *
 * Provides type-safe HTTP requests with automatic CSRF protection,
 * error handling, and authentication cookie management.
 */

const API_BASE = import.meta.env.PUBLIC_API_URL || "https://localhost";

/**
 * Read a cookie value by name from document.cookie.
 *
 * The CSRF cookie is intentionally set as non-HttpOnly so JavaScript can read it
 * for inclusion in request headers during state-mutating operations.
 *
 * @param name - Cookie name to retrieve
 * @returns Cookie value or null if not found
 */
function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.split("=")[1]) : null;
}

/** HTTP methods that mutate state and require CSRF protection. */
const UNSAFE_METHODS = new Set(["POST", "PUT", "DELETE", "PATCH"]);

interface FetchOptions extends RequestInit {
  /** URL query parameters for GET requests */
  params?: Record<string, string>;
}

/**
 * HTTP client with automatic CSRF protection and error handling.
 *
 * Handles API communication with proper authentication, CSRF token inclusion,
 * and standardized error responses for consistent frontend behavior.
 */
class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
  }

  /**
   * Make HTTP request with automatic error handling and CSRF protection.
   *
   * @param endpoint - API endpoint path (without /api prefix)
   * @param options - Request options including method, body, and headers
   * @returns Typed response data
   * @throws Error with API error details
   */
  async fetch<T>(endpoint: string, options: FetchOptions = {}): Promise<T> {
    const { params, ...fetchOptions } = options;
    let url = `${this.baseUrl}/api${endpoint}`;

    if (params) {
      const searchParams = new URLSearchParams(params);
      url += `?${searchParams.toString()}`;
    }

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(fetchOptions.headers as Record<string, string>),
    };

    const method = (fetchOptions.method || "GET").toUpperCase();
    if (UNSAFE_METHODS.has(method)) {
      const csrfToken = getCookie("csrf_token");
      if (csrfToken) {
        headers["X-CSRF-Token"] = csrfToken;
      }
    }

    const response = await fetch(url, {
      credentials: "include",
      ...fetchOptions,
      headers,
    });

    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: "Unknown error" }));
      throw new Error(error.detail || `API error: ${response.status}`);
    }

    return response.json();
  }

  async get<T>(endpoint: string, params?: Record<string, string>): Promise<T> {
    return this.fetch<T>(endpoint, { method: "GET", params });
  }

  async post<T>(endpoint: string, body?: unknown): Promise<T> {
    return this.fetch<T>(endpoint, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  async put<T>(endpoint: string, body?: unknown): Promise<T> {
    return this.fetch<T>(endpoint, {
      method: "PUT",
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  async delete<T>(endpoint: string): Promise<T> {
    return this.fetch<T>(endpoint, { method: "DELETE" });
  }
}

export const api = new ApiClient(API_BASE);
