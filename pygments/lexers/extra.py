# Changes to this file by The Ampify Authors are according to the
# Public Domain license that can be found in the root LICENSE file.

from pygments.lexer import RegexLexer, bygroups
from pygments.token import (
    Punctuation, Text, Comment, Operator, Keyword, Name, Number
    )

__all__ = ['PBLexer']

# By Nick Gerakines <http://github.com/ngerakines>
# Found at http://blog.socklabs.com/2008/12/23/protocol_buffers_and_pygments/

class PBLexer(RegexLexer):
    """
    A simple Protocol Buffer lexer useful for parsing .proto files.
    Contributed by Nick Gerakines <nick@gerakines.net>.
    """

    name = 'ProtocolBuffers'
    aliases = ['pb']
    filenames = ['*.proto']
    mimetypes = ['text/pb']

    tokens = {
        'root': [
            (r'(message)(\s+)(\w+)', bygroups(Keyword.Reserved, Text.Whitespace, Name.Class)),
            (r'(required|optional|repeated)', Keyword.Reserved),
            (r'(string|int32)', Keyword.Type),
            (r'({|})', Punctuation),
            (r'([^ ]*)(\s+)(=)(\s+)(\d+);', bygroups(Name.Label, Text.Whitespace, Punctuation, Text.Whitespace, Number.Integer)),
            (r'=', Operator),
            (r';', Operator),
            (r'//.*?$', Comment),
            (r'[0-9]+', Number.Integer),
            (r'\s+', Text.Whitespace),
        ],
    }
