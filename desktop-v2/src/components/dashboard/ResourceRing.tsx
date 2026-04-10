interface Props {
  label: string;
  percent: number;
  color?: string;
}

export function ResourceRing({ label, percent, color = "#a8c4f0" }: Props) {
  const radius = 36;
  const stroke = 6;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (percent / 100) * circumference;

  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={90} height={90} className="-rotate-90">
        <circle
          cx={45} cy={45} r={radius}
          fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={stroke}
        />
        <circle
          cx={45} cy={45} r={radius}
          fill="none" stroke={color} strokeWidth={stroke}
          strokeDasharray={circumference} strokeDashoffset={offset}
          strokeLinecap="round"
          className="transition-all duration-500"
        />
      </svg>
      <div className="text-sm text-text-primary -mt-[58px] mb-[30px]">
        {percent}%
      </div>
      <div className="text-xs text-text-secondary">{label}</div>
    </div>
  );
}
