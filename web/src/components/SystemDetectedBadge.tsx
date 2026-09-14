import { Badge, Tooltip } from '@mantine/core';
import { IconRobotOff } from '@tabler/icons-react';

/** Marks rule findings: deterministic rules over imported data, not AI output. */
export function SystemDetectedBadge({ size = 'xs' }: { size?: 'xs' | 'sm' }) {
  return (
    <Tooltip label="Detected by a deterministic rule over the imported data (not AI)" withArrow multiline w={240}>
      <Badge size={size} variant="outline" color="gray" leftSection={<IconRobotOff size={10} />} style={{ cursor: 'help' }}>
        System-detected
      </Badge>
    </Tooltip>
  );
}
