import { getFileIcon } from "./FileIcons";

export function TurnWrittenFiles({
  paths,
  total,
  onOpenFile,
}: {
  paths: readonly string[];
  total: number;
  onOpenFile: (path: string) => void;
}) {
  if (paths.length === 0) return null;
  return (
    <div className="turn-written-files" aria-label="本次工具变更的文件">
      {paths.map((path) => {
        const name = path.replaceAll("\\", "/").split("/").at(-1) ?? path;
        return (
          <button
            key={path}
            type="button"
            title={path}
            aria-label={`打开变更文件 ${name}`}
            onClick={() => onOpenFile(path)}
          >
            {getFileIcon(name, 12)}
            <span>{name}</span>
          </button>
        );
      })}
      {total > paths.length && <small>另有 {total - paths.length} 项</small>}
    </div>
  );
}
