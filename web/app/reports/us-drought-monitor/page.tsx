import type { Metadata } from "next";

import { T } from "@/components/chrome/I18n";
import TopBar from "@/components/chrome/TopBar";
import UsDroughtMonitorView from "@/components/drought/UsDroughtMonitorView";
import { releaseInfo } from "@/lib/dashboard";
import { getUsDroughtMonitorIndex, takeFiles } from "@/lib/data";

export const metadata: Metadata = { title: "U.S. Drought Monitor (NDMC) · USDA Reports Dashboard" };

const DATASETS = new Set(["us_drought_monitor:conus"]);

export default function UsDroughtMonitorPage() {
  const index = getUsDroughtMonitorIndex();
  if (!index) {
    return (
      <>
        <TopBar title={<T k="report.us_drought_monitor" />} release={releaseInfo()} />
        <p className="m-0 text-muted">
          <T k="udm.none" />
        </p>
      </>
    );
  }
  return <UsDroughtMonitorView index={index} take={takeFiles(DATASETS)} release={releaseInfo()} />;
}
