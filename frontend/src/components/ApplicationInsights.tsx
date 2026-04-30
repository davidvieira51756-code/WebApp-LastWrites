"use client";

import { useEffect } from "react";

import { ApplicationInsights } from "@microsoft/applicationinsights-web";

let appInsightsInstance: ApplicationInsights | null = null;

export default function ApplicationInsightsBootstrap() {
  useEffect(() => {
    const connectionString =
      process.env.NEXT_PUBLIC_APPLICATIONINSIGHTS_CONNECTION_STRING?.trim() || "";
    if (!connectionString || appInsightsInstance) {
      return;
    }

    const instance = new ApplicationInsights({
      config: {
        connectionString,
        enableAutoRouteTracking: true,
      },
    });
    instance.loadAppInsights();
    instance.trackPageView();
    appInsightsInstance = instance;
  }, []);

  return null;
}
