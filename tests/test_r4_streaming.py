"""Large experimental JSON output stays streaming and structurally unchanged."""
import io,json,unittest
from unittest.mock import patch
from test_historical_segments import M,G
class SegmentStreaming(unittest.TestCase):
    def test_same_json_with_incremental_writes(self):
        class Sink(io.StringIO):
            sizes=[]
            def write(self,text):
                self.sizes.append(len(text));return super().write(text)
        sink=Sink();data={'说明':'条件估计','records':[{'index':i,'value':i/3}for i in range(400)]}
        with patch.object(G['sys'],'stdout',sink):M['write_segment_json_stdout'](data)
        self.assertEqual(sink.getvalue(),json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2)+'\n')
        self.assertEqual(json.loads(sink.getvalue()),data)
        self.assertGreater(len(sink.sizes),400)
        self.assertLess(max(sink.sizes),100)
    def test_nonfinite_values_remain_rejected(self):
        with patch.object(G['sys'],'stdout',io.StringIO()),self.assertRaises(ValueError):
            M['write_segment_json_stdout']({'value':float('nan')})
