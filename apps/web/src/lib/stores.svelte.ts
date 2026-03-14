/**
 * User authentication state management with Svelte 5 runes.
 *
 * Provides reactive user state, loading indicators, and authentication
 * actions for Moose Sports Empire frontend components.
 */

import { navigate } from "astro:transitions/client";
import { api } from "./api";

/** User profile data structure from backend API */
interface User {
  id: number;
  yahoo_guid: string;
  display_name: string;
  role: string;
}

let currentUser = $state<User | null>(null);
let isLoading = $state(true);

export function getUser() {
  return currentUser;
}

export function getIsLoading() {
  return isLoading;
}

/**
 * Fetch current user from authentication endpoint.
 *
 * Updates reactive state and handles authentication errors gracefully.
 * Called on app initialization and after login/logout actions.
 */
export async function fetchUser() {
  isLoading = true;
  const previousUser = currentUser;
  try {
    currentUser = await api.get<User>("/auth/me");
  } catch {
    // Expected 401 for logged-out users - not an error condition
    // Only nullify if no previous user was known (prevents flicker on transient failures)
    if (!previousUser) {
      currentUser = null;
    }
  } finally {
    isLoading = false;
  }
}

/**
 * Log out user and clear authentication state.
 *
 * Calls backend logout endpoint, clears local state, and redirects
 * to home page. Ignores API errors to ensure logout completes.
 */
export async function logout() {
  currentUser = null;
  isLoading = false;
  try {
    await api.post("/auth/logout");
  } catch {
    // Ignore API errors - logout should complete regardless
  }
  navigate("/", { history: "replace" });
}
