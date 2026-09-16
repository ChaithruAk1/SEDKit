import {
  IconAlertTriangle,
  IconApps,
  IconBuildingFactory2,
  IconChartBar,
  IconCurrencyEuro,
  IconDatabase,
  IconFileText,
  IconLayoutDashboard,
  IconListCheck,
  IconPoint,
  IconRobot,
  IconTicket,
  type Icon,
} from '@tabler/icons-react';

/** Server nav icons are Tabler icon names; unknown names fall back to a dot. Bundled, no icon font or CDN. */
const ICONS: Record<string, Icon> = {
  'layout-dashboard': IconLayoutDashboard,
  'alert-triangle': IconAlertTriangle,
  ticket: IconTicket,
  apps: IconApps,
  'building-factory': IconBuildingFactory2,
  'currency-euro': IconCurrencyEuro,
  database: IconDatabase,
  'chart-bar': IconChartBar,
  'file-text': IconFileText,
  'list-check': IconListCheck,
  robot: IconRobot,
};

export function NavIcon({ name, size = 18 }: { name: string | null | undefined; size?: number }) {
  const Component = (name ? ICONS[name] : undefined) ?? IconPoint;
  return <Component size={size} stroke={1.6} aria-hidden />;
}
