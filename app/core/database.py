from supabase import create_client, Client
from app.core.config import settings
import httpx


# supabase-py 2.x builds its PostgREST client on top of httpx.
# By default it reuses a single persistent connection. Supabase's PostgREST
# closes idle connections server-side (~60s), which causes:
#   httpx.RemoteProtocolError: Server disconnected
# on the first request after an idle period.
#
# Fix: pass a custom httpx client with:
#   - keep-alive timeout short enough that WE close before the server does
#   - connection pool limits so stale sockets are recycled
#   - Retry-on-disconnect at the transport level (httpx retries=1)
def _make_http_client() -> httpx.Client:
    transport = httpx.HTTPTransport(
        retries=1,  # retry once on RemoteProtocolError / connection reset
    )
    return httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(
            max_keepalive_connections=5,
            max_connections=10,
            keepalive_expiry=30,  # drop idle conn after 30s (Supabase drops ~60s)
        ),
    )


class Database:
    _client: Client = None

    @classmethod
    def get_client(cls) -> Client:
        """
        Returns a Supabase client using the SERVICE ROLE KEY.
        This bypasses Row Level Security (RLS) for backend operations.

        IMPORTANT: Never expose this client or key to the frontend.
        Frontend should use SUPABASE_KEY (anon) which respects RLS.

        The custom httpx client prevents RemoteProtocolError: Server disconnected
        by expiring keepalive connections before Supabase's server-side timeout.
        """
        if cls._client is None:
            cls._client = create_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_SERVICE_ROLE_KEY,
                options={"http_client": _make_http_client()},
            )
        return cls._client


# Global instance accessor (backend use only - bypasses RLS)
db = Database.get_client()

