/**
 * Theme Initialization Script
 * 
 * This script initializes the theme system and should be imported in the main layout.
 * It sets up CSS custom properties and provides theme switching functionality.
 */

import { browser } from '$app/environment';
import { currentTheme, setTheme, type ThemeConfig } from './theme';
import { reapplyBrandingPrimary } from './brandingPrimary';

// Initialize theme on client-side
export function initializeTheme(): void {
  if (!browser) return;
  
  // Set up CSS custom properties
  updateCSSVariables();
  
  // Listen for theme changes (if you want to support system theme switching)
  if (window.matchMedia) {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    mediaQuery.addEventListener('change', handleSystemThemeChange);
  }
}

// Update CSS custom properties based on current theme
function updateCSSVariables(): void {
  if (!browser) return;
  
  const root = document.documentElement;
  
  // Update color variables
  Object.entries(currentTheme.colors.primary).forEach(([key, value]) => {
    root.style.setProperty(`--color-primary-${key}`, value);
  });
  
  Object.entries(currentTheme.colors.success).forEach(([key, value]) => {
    root.style.setProperty(`--color-success-${key}`, value);
  });
  
  Object.entries(currentTheme.colors.warning).forEach(([key, value]) => {
    root.style.setProperty(`--color-warning-${key}`, value);
  });
  
  Object.entries(currentTheme.colors.error).forEach(([key, value]) => {
    root.style.setProperty(`--color-error-${key}`, value);
  });
  
  Object.entries(currentTheme.colors.gray).forEach(([key, value]) => {
    root.style.setProperty(`--color-gray-${key}`, value);
  });
  
  Object.entries(currentTheme.colors.text).forEach(([key, value]) => {
    root.style.setProperty(`--color-text-${key}`, value);
  });
  
  Object.entries(currentTheme.colors.background).forEach(([key, value]) => {
    root.style.setProperty(`--color-bg-${key}`, value);
  });
  
  Object.entries(currentTheme.colors.border).forEach(([key, value]) => {
    root.style.setProperty(`--color-border-${key}`, value);
  });
  
  // Update typography variables
  root.style.setProperty('--font-family-sans', currentTheme.typography.fontFamily.sans.join(', '));
  root.style.setProperty('--font-family-serif', currentTheme.typography.fontFamily.serif.join(', '));
  root.style.setProperty('--font-family-mono', currentTheme.typography.fontFamily.mono.join(', '));
  
  Object.entries(currentTheme.typography.fontSize).forEach(([key, value]) => {
    root.style.setProperty(`--font-size-${key}`, value);
  });
  
  Object.entries(currentTheme.typography.fontWeight).forEach(([key, value]) => {
    root.style.setProperty(`--font-weight-${key}`, value.toString());
  });
  
  // Update spacing variables
  Object.entries(currentTheme.spacing).forEach(([key, value]) => {
    root.style.setProperty(`--spacing-${key}`, value);
  });
  
  // Update border radius variables
  Object.entries(currentTheme.borderRadius).forEach(([key, value]) => {
    root.style.setProperty(`--border-radius-${key}`, value);
  });
  
  // Update shadow variables
  Object.entries(currentTheme.shadows).forEach(([key, value]) => {
    root.style.setProperty(`--shadow-${key}`, value);
  });
  
  // Update transition variables
  Object.entries(currentTheme.transitions).forEach(([key, value]) => {
    root.style.setProperty(`--transition-${key}`, value);
  });

  // The primary scale is the realm's colour, not the theme's neutral one written above.
  reapplyBrandingPrimary();
}

// Handle system theme changes (optional feature)
function handleSystemThemeChange(e: MediaQueryListEvent): void {
  // You can implement automatic theme switching based on system preference here
  // For now, we'll just log the change
  console.log('System theme changed to:', e.matches ? 'dark' : 'light');
}

// Theme switching functions
export function switchToLightTheme(): void {
  // You can define a light theme variant here
  // For now, we'll use the default theme
  setTheme(currentTheme);
}

export function switchToDarkTheme(): void {
  // You can define a dark theme variant here
  const darkTheme: Partial<ThemeConfig> = {
    colors: {
      ...currentTheme.colors,
      text: {
        primary: '#FFFFFF',
        secondary: '#D1D5DB',
        tertiary: '#9CA3AF',
        inverse: '#111827',
        disabled: '#6B7280'
      },
      background: {
        primary: '#111827',
        secondary: '#1F2937',
        tertiary: '#374151',
        inverse: '#FFFFFF',
        overlay: 'rgba(255, 255, 255, 0.1)'
      },
      border: {
        primary: '#374151',
        secondary: '#4B5563',
        focus: '#60A5FA',
        error: '#F87171'
      }
    }
  };
  
  setTheme(darkTheme);
}

// Custom theme application
export function applyCustomTheme(customTheme: Partial<ThemeConfig>): void {
  setTheme(customTheme);
}

// Export the update function for external use
export { updateCSSVariables };
