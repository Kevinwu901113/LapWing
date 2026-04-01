import AceEditor from "react-ace";
import "ace-builds/src-noconflict/mode-markdown";
import "ace-builds/src-noconflict/theme-one_dark";
import "ace-builds/src-noconflict/ext-language_tools";

type MarkdownEditorProps = {
  value: string;
  onChange: (v: string) => void;
  readOnly?: boolean;
  height?: string;
};

export default function MarkdownEditor({
  value,
  onChange,
  readOnly = false,
  height = "400px",
}: MarkdownEditorProps) {
  return (
    <AceEditor
      mode="markdown"
      theme="one_dark"
      value={value}
      onChange={onChange}
      readOnly={readOnly}
      width="100%"
      height={height}
      fontSize={13}
      showPrintMargin={false}
      wrapEnabled={true}
      setOptions={{ useWorker: false }}
      editorProps={{ $blockScrolling: true }}
    />
  );
}
