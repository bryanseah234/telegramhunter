import { createClient } from '@supabase/supabase-js'

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || 'https://example.supabase.co'
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_KEY || 'build-time-placeholder-key'

export const supabase = createClient(supabaseUrl, supabaseAnonKey)
