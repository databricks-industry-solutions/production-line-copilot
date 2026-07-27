/**
 * Catches rendering/runtime failures for dashboard subtrees and shows a safe fallback card.
 * Used around major panels in `App` and again at root in `main.tsx`.
 * Exposes stack traces only in dev builds so production operators see actionable but non-sensitive errors.
 */
import React, { Component } from 'react';
import type { ReactNode } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@databricks/appkit-ui/react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: React.ErrorInfo | null;
}

const IS_DEV = import.meta.env?.DEV ?? false;

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
    };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error);
    console.error('Error details:', errorInfo);
    this.setState({
      error,
      errorInfo,
    });
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="p-4">
          <Card className="max-w-2xl mx-auto">
            <CardHeader>
              <CardTitle className="text-destructive">Something went wrong</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <p className="text-sm text-muted-foreground">
                  {this.state.error?.message || 'An unexpected error occurred in this section.'}
                </p>
                <button
                  type="button"
                  onClick={this.handleRetry}
                  className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
                >
                  Try Again
                </button>
                {IS_DEV && this.state.errorInfo && (
                  <details className="mt-4">
                    <summary className="cursor-pointer text-xs text-muted-foreground">Debug details (dev only)</summary>
                    <pre className="mt-2 bg-muted p-3 rounded text-xs overflow-auto max-h-64">
                      {this.state.error?.stack}
                      {'\n\nComponent Stack:'}
                      {this.state.errorInfo.componentStack}
                    </pre>
                  </details>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      );
    }

    return this.props.children;
  }
}
